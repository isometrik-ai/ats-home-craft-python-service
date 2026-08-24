"""Parking allotment admin API (project-scoped)."""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.schemas.parking_allotment import (
    AllotParkingSlotRequest,
    BlockParkingSlotRequest,
    ParkingAllotmentSlotDetailApiResponse,
    ParkingAllotmentSlotHistoryApiResponse,
    ParkingAllotmentSlotListApiResponse,
    ParkingAllotmentSlotListQuery,
    ParkingAllotmentSlotMutationApiResponse,
    ParkingAllotmentSummaryApiResponse,
    ParkingAllotmentSummaryQuery,
    ParkingAllotmentUnitListApiResponse,
    ParkingAllotmentUnitListQuery,
    ReassignParkingSlotRequest,
    ReleaseParkingSlotRequest,
    UnitAllotParkingSlotRequest,
)
from apps.user_service.app.services.parking_allotment_service import (
    ParkingAllotmentService,
)
from apps.user_service.app.utils.audit_context import set_audit_context
from apps.user_service.app.utils.common_utils import (
    ensure_staff_project_access,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import (
    PROJECTS_MANAGEMENT_EDIT,
    PROJECTS_MANAGEMENT_VIEW,
)
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/projects", tags=["Parking Allotment"])

COMMON_ERROR_RESPONSES: dict[int | str, dict] = {
    401: {"description": "Unauthorized (missing/invalid JWT)."},
    403: {"description": "Forbidden (insufficient permissions)."},
    404: {"description": "Not found."},
    409: {"description": "Conflict."},
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
    return {
        **COMMON_ERROR_RESPONSES,
        status_code: {"model": model, "description": description},
    }


SUMMARY_RESPONSES = _ok_response(
    ParkingAllotmentSummaryApiResponse,
    "Parking allotment dashboard summary counts.",
)
SLOT_LIST_RESPONSES = _ok_response(
    ParkingAllotmentSlotListApiResponse,
    "Paginated parking slots for the by-slot view.",
)
UNIT_LIST_RESPONSES = _ok_response(
    ParkingAllotmentUnitListApiResponse,
    "Paginated units for the by-unit parking view.",
)
SLOT_DETAIL_RESPONSES = _ok_response(
    ParkingAllotmentSlotDetailApiResponse,
    "Parking slot detail.",
)
SLOT_HISTORY_RESPONSES = _ok_response(
    ParkingAllotmentSlotHistoryApiResponse,
    "Parking slot audit history.",
)
SLOT_MUTATION_RESPONSES = _ok_response(
    ParkingAllotmentSlotMutationApiResponse,
    "Parking slot after allotment mutation.",
)


@handle_api_exceptions("get parking allotment summary")
@router.get(
    "/{project_id}/parking-allotment/summary",
    status_code=http_status.HTTP_200_OK,
    summary="Parking allotment dashboard summary",
    response_model=None,
    responses=SUMMARY_RESPONSES,
)
@limiter.limit("100/minute")
async def get_parking_allotment_summary(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    query: ParkingAllotmentSummaryQuery = Depends(),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return summary cards for the parking allotment screen."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
        request=request,
    )
    service = ParkingAllotmentService(db_connection=db_connection, user_context=user_context)
    data = await service.get_summary(
        project_id=project_id,
        tower_id=query.tower_id,
        facility_id=query.facility_id,
    )
    return success_response(
        request=request,
        message_key="parking_allotment.success.summary_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )


@handle_api_exceptions("list parking allotment slots")
@router.get(
    "/{project_id}/parking-allotment/slots",
    status_code=http_status.HTTP_200_OK,
    summary="List parking slots (by-slot view)",
    response_model=None,
    responses=SLOT_LIST_RESPONSES,
)
@limiter.limit("100/minute")
async def list_parking_allotment_slots(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    query: ParkingAllotmentSlotListQuery = Depends(),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List parking slots with filters for the by-slot admin table."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
        request=request,
    )
    service = ParkingAllotmentService(db_connection=db_connection, user_context=user_context)
    items, total = await service.list_slots(
        project_id=project_id,
        tower_id=query.tower_id,
        facility_id=query.facility_id,
        floor_level=query.floor_level,
        slot_type=query.slot_type.value if query.slot_type else None,
        status=query.status.value if query.status else None,
        search=query.search,
        page=query.page,
        page_size=query.page_size,
    )
    return list_response(
        request=request,
        message_key="parking_allotment.success.slots_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        items=[item.model_dump() for item in items],
        total=total,
        page=query.page,
        page_size=query.page_size,
    )


@handle_api_exceptions("get parking allotment slot detail")
@router.get(
    "/{project_id}/parking-allotment/slots/{slot_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Get parking slot detail",
    response_model=None,
    responses=SLOT_DETAIL_RESPONSES,
)
@limiter.limit("100/minute")
async def get_parking_allotment_slot_detail(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    slot_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return one parking slot with current unit allotment metadata."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
        request=request,
    )
    service = ParkingAllotmentService(db_connection=db_connection, user_context=user_context)
    data = await service.get_slot_detail(project_id=project_id, slot_id=slot_id)
    return success_response(
        request=request,
        message_key="parking_allotment.success.slot_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )


@handle_api_exceptions("list parking slot history")
@router.get(
    "/{project_id}/parking-allotment/slots/{slot_id}/history",
    status_code=http_status.HTTP_200_OK,
    summary="List parking slot history",
    response_model=None,
    responses=SLOT_HISTORY_RESPONSES,
)
@limiter.limit("100/minute")
async def list_parking_allotment_slot_history(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    slot_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return audit events for one parking slot."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
        request=request,
    )
    service = ParkingAllotmentService(db_connection=db_connection, user_context=user_context)
    items = await service.list_slot_history(project_id=project_id, slot_id=slot_id)
    return success_response(
        request=request,
        message_key="parking_allotment.success.history_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=[item.model_dump() for item in items],
    )


@handle_api_exceptions("list parking allotment units")
@router.get(
    "/{project_id}/parking-allotment/units",
    status_code=http_status.HTTP_200_OK,
    summary="List units (by-unit view)",
    response_model=None,
    responses=UNIT_LIST_RESPONSES,
)
@limiter.limit("100/minute")
async def list_parking_allotment_units(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    query: ParkingAllotmentUnitListQuery = Depends(),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List units with entitlement and held slots for the by-unit table."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
        request=request,
    )
    service = ParkingAllotmentService(db_connection=db_connection, user_context=user_context)
    items, total = await service.list_units(
        project_id=project_id,
        tower_id=query.tower_id,
        entitlement_status=query.entitlement_status,
        search=query.search,
        page=query.page,
        page_size=query.page_size,
    )
    return list_response(
        request=request,
        message_key="parking_allotment.success.units_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        items=[item.model_dump() for item in items],
        total=total,
        page=query.page,
        page_size=query.page_size,
    )


@handle_api_exceptions("allot parking slot to unit")
@router.post(
    "/{project_id}/parking-allotment/slots/{slot_id}/allot",
    status_code=http_status.HTTP_200_OK,
    summary="Allot a parking slot to a unit",
    response_model=None,
    responses=SLOT_MUTATION_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="general",
    compliance_tags=["audit_required"],
    table_name="unit_parking_allotments",
    category="PARKING_ALLOTMENT",
)
async def allot_parking_slot_to_unit(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    slot_id: str = Path(...),
    body: AllotParkingSlotRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Allot a free resident slot to a unit."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = ParkingAllotmentService(db_connection=db_connection, user_context=user_context)
    data = await service.allot_slot(project_id=project_id, slot_id=slot_id, body=body)
    set_audit_context(
        request,
        user_context,
        table="unit_parking_allotments",
        requested_id=slot_id,
        description=f"Allotted parking slot {slot_id} to unit {body.unit_id}",
        risk_level="medium",
        new_data=data.model_dump(),
    )
    return success_response(
        request=request,
        message_key="parking_allotment.success.slot_allotted",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )


@handle_api_exceptions("reassign parking slot")
@router.post(
    "/{project_id}/parking-allotment/slots/{slot_id}/reassign",
    status_code=http_status.HTTP_200_OK,
    summary="Reassign a parking slot to another unit",
    response_model=None,
    responses=SLOT_MUTATION_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="general",
    compliance_tags=["audit_required"],
    table_name="unit_parking_allotments",
    category="PARKING_ALLOTMENT",
)
async def reassign_parking_slot(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    slot_id: str = Path(...),
    body: ReassignParkingSlotRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Move an allotted slot from one unit to another."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = ParkingAllotmentService(db_connection=db_connection, user_context=user_context)
    data = await service.reassign_slot(project_id=project_id, slot_id=slot_id, body=body)
    set_audit_context(
        request,
        user_context,
        table="unit_parking_allotments",
        requested_id=slot_id,
        description=f"Reassigned parking slot {slot_id} to unit {body.unit_id}",
        risk_level="medium",
        new_data=data.model_dump(),
    )
    return success_response(
        request=request,
        message_key="parking_allotment.success.slot_reassigned",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )


@handle_api_exceptions("release parking slot")
@router.post(
    "/{project_id}/parking-allotment/slots/{slot_id}/release",
    status_code=http_status.HTTP_200_OK,
    summary="Release a parking slot from its unit",
    response_model=None,
    responses=SLOT_MUTATION_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="general",
    compliance_tags=["audit_required"],
    table_name="unit_parking_allotments",
    category="PARKING_ALLOTMENT",
)
async def release_parking_slot(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    slot_id: str = Path(...),
    body: ReleaseParkingSlotRequest | None = Body(None),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Release an allotted slot back to free inventory."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = ParkingAllotmentService(db_connection=db_connection, user_context=user_context)
    data = await service.release_slot(
        project_id=project_id,
        slot_id=slot_id,
        body=body or ReleaseParkingSlotRequest(),
    )
    set_audit_context(
        request,
        user_context,
        table="unit_parking_allotments",
        requested_id=slot_id,
        description=f"Released parking slot {slot_id}",
        risk_level="medium",
        new_data=data.model_dump(),
    )
    return success_response(
        request=request,
        message_key="parking_allotment.success.slot_released",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )


@handle_api_exceptions("block parking slot")
@router.post(
    "/{project_id}/parking-allotment/slots/{slot_id}/block",
    status_code=http_status.HTTP_200_OK,
    summary="Block a free parking slot",
    response_model=None,
    responses=SLOT_MUTATION_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="general",
    compliance_tags=["audit_required"],
    table_name="facility_parking_slots",
    category="PARKING_ALLOTMENT",
)
async def block_parking_slot(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    slot_id: str = Path(...),
    body: BlockParkingSlotRequest | None = Body(None),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Block a free slot so it cannot be allotted."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = ParkingAllotmentService(db_connection=db_connection, user_context=user_context)
    data = await service.block_slot(
        project_id=project_id,
        slot_id=slot_id,
        body=body or BlockParkingSlotRequest(),
    )
    set_audit_context(
        request,
        user_context,
        table="facility_parking_slots",
        requested_id=slot_id,
        description=f"Blocked parking slot {slot_id}",
        risk_level="low",
        new_data=data.model_dump(),
    )
    return success_response(
        request=request,
        message_key="parking_allotment.success.slot_blocked",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )


@handle_api_exceptions("unblock parking slot")
@router.post(
    "/{project_id}/parking-allotment/slots/{slot_id}/unblock",
    status_code=http_status.HTTP_200_OK,
    summary="Unblock a parking slot",
    response_model=None,
    responses=SLOT_MUTATION_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="general",
    compliance_tags=["audit_required"],
    table_name="facility_parking_slots",
    category="PARKING_ALLOTMENT",
)
async def unblock_parking_slot(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    slot_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Restore a blocked slot to free inventory."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = ParkingAllotmentService(db_connection=db_connection, user_context=user_context)
    data = await service.unblock_slot(project_id=project_id, slot_id=slot_id)
    set_audit_context(
        request,
        user_context,
        table="facility_parking_slots",
        requested_id=slot_id,
        description=f"Unblocked parking slot {slot_id}",
        risk_level="low",
        new_data=data.model_dump(),
    )
    return success_response(
        request=request,
        message_key="parking_allotment.success.slot_unblocked",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )


@handle_api_exceptions("allot parking slot from unit view")
@router.post(
    "/{project_id}/parking-allotment/units/{unit_id}/allot",
    status_code=http_status.HTTP_200_OK,
    summary="Allot a slot to a unit (by-unit view)",
    response_model=None,
    responses=SLOT_MUTATION_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="general",
    compliance_tags=["audit_required"],
    table_name="unit_parking_allotments",
    category="PARKING_ALLOTMENT",
)
async def allot_parking_slot_from_unit(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    unit_id: str = Path(...),
    body: UnitAllotParkingSlotRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Allot a specific slot to a unit from the by-unit screen."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = ParkingAllotmentService(db_connection=db_connection, user_context=user_context)
    data = await service.allot_slot_to_unit(project_id=project_id, unit_id=unit_id, body=body)
    set_audit_context(
        request,
        user_context,
        table="unit_parking_allotments",
        requested_id=body.slot_id,
        description=f"Allotted slot {body.slot_id} to unit {unit_id}",
        risk_level="medium",
        new_data=data.model_dump(),
    )
    return success_response(
        request=request,
        message_key="parking_allotment.success.slot_allotted",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )
