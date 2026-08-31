"""Resident walk-in approval API (contact onboarding context)."""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.schemas.walk_in import (
    RejectWalkInVisitUnitRequest,
    ResidentWalkInVisitUnitListQuery,
)
from apps.user_service.app.services.walk_in_service import WalkInService
from apps.user_service.app.utils.audit_context import set_audit_context
from apps.user_service.app.utils.common_utils import (
    extract_onboarding_contact_context,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/walk-ins", tags=["Walk-in"])

COMMON_ERROR_RESPONSES: dict[int | str, dict] = {
    401: {"description": "Unauthorized (missing/invalid JWT)."},
    403: {"description": "Forbidden."},
    404: {"description": "Not found."},
    422: {"description": "Validation error."},
    429: {"description": "Too many requests (rate limited)."},
    500: {"description": "Internal server error."},
}


@handle_api_exceptions("list resident walk-in visit units")
@router.get(
    "/visit-units",
    status_code=http_status.HTTP_200_OK,
    summary="List walk-in visit units for my flats",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("60/minute")
async def list_resident_visit_units(
    request: Request,
    query: ResidentWalkInVisitUnitListQuery = Depends(),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return visit units on flats the resident occupies (all statuses unless filtered)."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = WalkInService(db_connection=db_connection, user_context=user_context)
    items = await service.list_resident_visit_units(
        contact_id=str(contact["id"]),
        query=query,
    )
    return list_response(
        request=request,
        items=items,
        total=len(items),
        page=1,
        page_size=max(len(items), 1),
        message_key="walk_in.success.visit_units_retrieved",
        custom_code=CustomStatusCode.SUCCESS if items else CustomStatusCode.NO_CONTENT,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("get resident walk-in detail")
@router.get(
    "/{walk_in_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Get walk-in visit details for resident",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("60/minute")
async def get_resident_walk_in(
    request: Request,
    walk_in_id: str = Path(..., description="Walk-in entry identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return walk-in detail when the resident has a linked flat on the visit."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = WalkInService(db_connection=db_connection, user_context=user_context)
    data = await service.get_resident_walk_in(
        contact_id=str(contact["id"]),
        walk_in_entry_id=walk_in_id,
    )
    return success_response(
        request=request,
        message_key="walk_in.success.retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("approve walk-in visit unit")
@router.post(
    "/{walk_in_id}/visit-units/{visit_unit_id}/approve",
    status_code=http_status.HTTP_200_OK,
    summary="Approve walk-in for my flat",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="walk_in_visit_units",
    category="CONTACT_ONBOARDING",
)
async def approve_visit_unit(
    request: Request,
    walk_in_id: str = Path(..., description="Walk-in entry identifier (UUID string)."),
    visit_unit_id: str = Path(..., description="Visit unit identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Resident approves a walk-in for their flat."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = WalkInService(db_connection=db_connection, user_context=user_context)
    data = await service.approve_visit_unit(
        contact_id=str(contact["id"]),
        walk_in_entry_id=walk_in_id,
        visit_unit_id=visit_unit_id,
    )
    set_audit_context(
        request,
        user_context,
        table="walk_in_visit_units",
        requested_id=visit_unit_id,
        description=f"Approved walk-in visit unit: {visit_unit_id}",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="walk_in.success.visit_unit_approved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("reject walk-in visit unit")
@router.post(
    "/{walk_in_id}/visit-units/{visit_unit_id}/reject",
    status_code=http_status.HTTP_200_OK,
    summary="Reject walk-in for my flat",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="walk_in_visit_units",
    category="CONTACT_ONBOARDING",
)
async def reject_visit_unit(
    request: Request,
    walk_in_id: str = Path(..., description="Walk-in entry identifier (UUID string)."),
    visit_unit_id: str = Path(..., description="Visit unit identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: RejectWalkInVisitUnitRequest = Body(default_factory=RejectWalkInVisitUnitRequest),
):
    """Resident rejects a walk-in for their flat."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = WalkInService(db_connection=db_connection, user_context=user_context)
    data = await service.reject_visit_unit(
        contact_id=str(contact["id"]),
        walk_in_entry_id=walk_in_id,
        visit_unit_id=visit_unit_id,
        body=body,
    )
    set_audit_context(
        request,
        user_context,
        table="walk_in_visit_units",
        requested_id=visit_unit_id,
        description=f"Rejected walk-in visit unit: {visit_unit_id}",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="walk_in.success.visit_unit_rejected",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )
