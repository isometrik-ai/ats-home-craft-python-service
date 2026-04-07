"""Lead Stages API Module."""

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.schemas.lead_stages import (
    CreateLeadStageRequest,
    UpdateLeadStageRequest,
)
from apps.user_service.app.services.lead_stage_service import LeadStageService
from apps.user_service.app.utils.common_utils import (
    check_permissions,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import (
    LEADS_MANAGEMENT_CREATE,
    LEADS_MANAGEMENT_DELETE,
    LEADS_MANAGEMENT_EDIT,
    LEADS_MANAGEMENT_VIEW,
)
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/lead-stages", tags=["Lead Stages"])


@handle_api_exceptions("create lead stage")
@router.post(
    "",
    status_code=http_status.HTTP_201_CREATED,
    description="Create a new lead stage for the authenticated organization",
    summary="Create lead stage",
    responses={
        http_status.HTTP_201_CREATED: {"description": "Lead stage created successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_409_CONFLICT: {
            "description": "Duplicate stage name or sort order conflict",
        },
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
    },
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="confidential",
    compliance_tags=[
        "soc2_audit",
        "audit_required",
    ],
    table_name="lead_stages",
    category="LEAD_STAGE",
)
async def create_lead_stage(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: CreateLeadStageRequest = Body(...),
):
    """Create a lead stage definition.

    Implements first-stage bootstrap, append/insert sort-order semantics,
    and uniqueness guarantees for stage_name/stage_key within organization.
    """
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=LEADS_MANAGEMENT_CREATE,
    )

    request.state.audit_table = "lead_stages"
    request.state.audit_description = f"Created lead stage: {body.stage_name}"
    request.state.audit_risk_level = "medium"
    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }

    lead_stage_service = LeadStageService(
        user_context=user_context,
        db_connection=db_connection,
    )
    created_stage = await lead_stage_service.create_lead_stage(body)
    request.state.audit_requested_id = str(created_stage.get("id", "")) if created_stage else ""
    request.state.raw_audit_new_data = created_stage

    return success_response(
        request=request,
        message_key="lead_stages.success.stage_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
    )


@handle_api_exceptions("list lead stages")
@router.get(
    "",
    status_code=http_status.HTTP_200_OK,
    description="List all lead stages for the authenticated organization",
    summary="List lead stages",
    responses={
        http_status.HTTP_200_OK: {"description": "Lead stages retrieved successfully"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
    },
)
@limiter.limit("100/minute")
async def list_lead_stages(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List lead stages ordered by sort_order."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=LEADS_MANAGEMENT_VIEW,
    )

    lead_stage_service = LeadStageService(
        user_context=user_context,
        db_connection=db_connection,
    )
    items, total = await lead_stage_service.list_lead_stages()

    if not items:
        return list_response(
            request=request,
            items=[],
            total=0,
            message_key="success.no_data",
            custom_code=CustomStatusCode.NO_CONTENT,
            status_code=http_status.HTTP_200_OK,
        )

    return list_response(
        request=request,
        items=items,
        total=total,
        message_key="lead_stages.success.stages_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("get lead stage")
@router.get(
    "/{stage_id}",
    status_code=http_status.HTTP_200_OK,
    description="Get a single lead stage by id",
    summary="Get lead stage",
    responses={
        http_status.HTTP_200_OK: {"description": "Lead stage retrieved successfully"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Lead stage not found"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
    },
)
@limiter.limit("100/minute")
async def get_lead_stage(
    request: Request,
    stage_id: str = Path(..., description="Lead stage ID"),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Get lead stage details for the authenticated organization."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=LEADS_MANAGEMENT_VIEW,
    )

    lead_stage_service = LeadStageService(
        user_context=user_context,
        db_connection=db_connection,
    )
    stage_data = await lead_stage_service.get_lead_stage(stage_id)

    return success_response(
        request=request,
        message_key="lead_stages.success.stage_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=stage_data,
    )


@handle_api_exceptions("update lead stage")
@router.patch(
    "/{stage_id}",
    status_code=http_status.HTTP_200_OK,
    description="Update a lead stage (partial update, including reorder)",
    summary="Update lead stage",
    responses={
        http_status.HTTP_200_OK: {"description": "Lead stage updated successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Validation error"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Lead stage not found"},
        http_status.HTTP_409_CONFLICT: {"description": "Stage conflict"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
    },
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="confidential",
    compliance_tags=[
        "soc2_audit",
        "audit_required",
    ],
    table_name="lead_stages",
    category="LEAD_STAGE",
)
async def update_lead_stage(
    request: Request,
    stage_id: str = Path(..., description="Lead stage ID"),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: UpdateLeadStageRequest = Body(...),
):
    """Update a lead stage for the authenticated organization."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=LEADS_MANAGEMENT_EDIT,
    )

    request.state.audit_table = "lead_stages"
    request.state.audit_requested_id = stage_id
    request.state.audit_description = f"Updated lead stage: {stage_id}"
    request.state.audit_risk_level = "medium"
    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }

    lead_stage_service = LeadStageService(
        user_context=user_context,
        db_connection=db_connection,
    )
    updated_stage = await lead_stage_service.update_lead_stage(stage_id=stage_id, body=body)
    request.state.raw_audit_new_data = updated_stage

    return success_response(
        request=request,
        message_key="lead_stages.success.stage_updated",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("delete lead stage")
@router.delete(
    "/{stage_id}",
    status_code=http_status.HTTP_200_OK,
    description="Delete a lead stage and compact sort_order for remaining stages",
    summary="Delete lead stage",
    responses={
        http_status.HTTP_200_OK: {"description": "Lead stage deleted successfully"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Lead stage not found"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
    },
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="confidential",
    compliance_tags=[
        "soc2_audit",
        "audit_required",
    ],
    table_name="lead_stages",
    category="LEAD_STAGE",
)
async def delete_lead_stage(
    request: Request,
    stage_id: str = Path(..., description="Lead stage ID"),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Hard-delete a lead stage for the authenticated organization."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=LEADS_MANAGEMENT_DELETE,
    )

    request.state.audit_table = "lead_stages"
    request.state.audit_requested_id = stage_id
    request.state.audit_description = f"Deleted lead stage: {stage_id}"
    request.state.audit_risk_level = "high"
    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }

    lead_stage_service = LeadStageService(
        user_context=user_context,
        db_connection=db_connection,
    )
    deleted_stage = await lead_stage_service.delete_lead_stage(stage_id)
    request.state.raw_audit_old_data = deleted_stage

    return success_response(
        request=request,
        message_key="lead_stages.success.stage_deleted",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )
