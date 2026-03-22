"""Leads API module."""

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Query, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.schemas.enums import LeadsListMode
from apps.user_service.app.schemas.leads import (
    CreateLeadRequest,
    LeadsListQueryParams,
    UpdateLeadRequest,
)
from apps.user_service.app.services.lead_service import LeadService
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

router = APIRouter(prefix="/leads", tags=["Leads"])


@handle_api_exceptions("create lead")
@router.post(
    "",
    status_code=http_status.HTTP_201_CREATED,
    description="Create a new lead linked to an existing client",
    summary="Create lead",
    responses={
        http_status.HTTP_201_CREATED: {"description": "Lead created successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Validation error"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Client, stage, or user not found"},
        http_status.HTTP_409_CONFLICT: {"description": "A lead already exists for this client"},
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
    table_name="leads",
    category="LEAD",
)
async def create_lead(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: CreateLeadRequest = Body(...),
):
    """Create a lead for the authenticated organization."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=LEADS_MANAGEMENT_CREATE,
    )

    request.state.audit_table = "leads"
    request.state.audit_description = f"Created lead for client: {body.client_id}"
    request.state.audit_risk_level = "medium"
    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }

    lead_service = LeadService(
        user_context=user_context,
        db_connection=db_connection,
    )
    created = await lead_service.create_lead(body)
    request.state.raw_audit_new_data = created

    return success_response(
        request=request,
        message_key="leads.success.lead_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
    )


@handle_api_exceptions("list leads")
@router.get(
    "",
    status_code=http_status.HTTP_200_OK,
    description="List leads (paginated list or kanban by stage)",
    summary="List leads",
    responses={
        http_status.HTTP_200_OK: {"description": "Leads retrieved successfully"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
    },
)
@limiter.limit("100/minute")
async def list_leads(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    mode: LeadsListMode = Query(..., description="list or kanban"),
    stage_id: str | None = Query(None, description="Filter by pipeline stage"),
    search: str | None = Query(None, description="Search by lead name or client name"),
    page: int = Query(1, ge=1, description="Page number (list mode)"),
    limit: int = Query(20, ge=1, le=100, description="Page size (list mode)"),
):
    """List leads for the authenticated organization."""
    params = LeadsListQueryParams(
        mode=mode,
        stage_id=stage_id,
        search=search,
        page=page,
        limit=limit,
    )
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=LEADS_MANAGEMENT_VIEW,
    )

    lead_service = LeadService(
        user_context=user_context,
        db_connection=db_connection,
    )
    result = await lead_service.list_leads(params)

    if params.mode == LeadsListMode.KANBAN:
        return success_response(
            request=request,
            message_key="leads.success.leads_retrieved",
            custom_code=CustomStatusCode.SUCCESS,
            status_code=http_status.HTTP_200_OK,
            data=result,
        )

    items, total, page_no = result
    if not items:
        return list_response(
            request=request,
            items=[],
            total=total,
            message_key="success.no_data",
            custom_code=CustomStatusCode.NO_CONTENT,
            status_code=http_status.HTTP_200_OK,
            page=page_no,
            page_size=params.limit,
        )

    return list_response(
        request=request,
        items=items,
        total=total,
        message_key="leads.success.leads_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        page=page_no,
        page_size=params.limit,
    )


@handle_api_exceptions("get lead")
@router.get(
    "/{lead_id}",
    status_code=http_status.HTTP_200_OK,
    description="Get a single lead by id",
    summary="Get lead",
    responses={
        http_status.HTTP_200_OK: {"description": "Lead retrieved successfully"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Lead not found"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
    },
)
@limiter.limit("100/minute")
async def get_lead(
    request: Request,
    lead_id: str = Path(..., description="Lead ID"),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Get lead details for the authenticated organization."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=LEADS_MANAGEMENT_VIEW,
    )

    lead_service = LeadService(
        user_context=user_context,
        db_connection=db_connection,
    )
    data = await lead_service.get_lead(lead_id)

    return success_response(
        request=request,
        message_key="leads.success.lead_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=data,
    )


@handle_api_exceptions("update lead")
@router.patch(
    "/{lead_id}",
    status_code=http_status.HTTP_200_OK,
    description="Update a lead (partial); custom_fields are merged with existing values",
    summary="Update lead",
    responses={
        http_status.HTTP_200_OK: {"description": "Lead updated successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Validation error"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Lead or related resource not found"},
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
    table_name="leads",
    category="LEAD",
)
async def update_lead(
    request: Request,
    lead_id: str = Path(..., description="Lead ID"),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: UpdateLeadRequest = Body(...),
):
    """Update a lead for the authenticated organization."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=LEADS_MANAGEMENT_EDIT,
    )

    request.state.audit_table = "leads"
    request.state.audit_requested_id = lead_id
    request.state.audit_description = f"Updated lead: {lead_id}"
    request.state.audit_risk_level = "medium"
    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }

    lead_service = LeadService(
        user_context=user_context,
        db_connection=db_connection,
    )
    updated = await lead_service.update_lead(lead_id=lead_id, body=body)
    request.state.raw_audit_new_data = updated

    return success_response(
        request=request,
        message_key="leads.success.lead_updated",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("delete lead")
@router.delete(
    "/{lead_id}",
    status_code=http_status.HTTP_200_OK,
    description="Hard-delete a lead (client record is not deleted)",
    summary="Delete lead",
    responses={
        http_status.HTTP_200_OK: {"description": "Lead deleted successfully"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Lead not found"},
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
    table_name="leads",
    category="LEAD",
)
async def delete_lead(
    request: Request,
    lead_id: str = Path(..., description="Lead ID"),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Hard-delete a lead for the authenticated organization."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=LEADS_MANAGEMENT_DELETE,
    )

    request.state.audit_table = "leads"
    request.state.audit_requested_id = lead_id
    request.state.audit_description = f"Deleted lead: {lead_id}"
    request.state.audit_risk_level = "high"
    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }

    lead_service = LeadService(
        user_context=user_context,
        db_connection=db_connection,
    )
    deleted = await lead_service.delete_lead(lead_id)
    request.state.raw_audit_old_data = deleted

    return success_response(
        request=request,
        message_key="leads.success.lead_deleted",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )
