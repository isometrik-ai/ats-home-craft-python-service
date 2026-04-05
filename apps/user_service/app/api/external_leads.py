"""External Leads API.

These endpoints are intended for external integrations (partners, embedded apps)
that do not authenticate with our JWT bearer token. Instead, the caller
authenticates via Isometrik credential decode using headers:

- ``licenseKey``
- ``appSecret``

The decoded ``projectId`` is mapped to our internal ``organization_id`` and all
operations are scoped to that organization.
"""

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Query, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn
from apps.user_service.app.dependencies.external_auth import get_organization_context
from apps.user_service.app.schemas.enums import LeadsListMode
from apps.user_service.app.schemas.leads import (
    CreateLeadRequest,
    LeadsListQueryParams,
    UpdateLeadRequest,
)
from apps.user_service.app.services.lead_service import LeadService
from apps.user_service.app.utils.common_utils import (
    UserContext,
    handle_api_exceptions,
    name_to_email_domain_label,
)
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/integrations/leads", tags=["Leads (External)"])


@handle_api_exceptions("external list leads")
@router.get(
    "",
    status_code=http_status.HTTP_200_OK,
    summary="List leads (external auth)",
    description=(
        "List leads for the organization resolved from Isometrik credentials "
        "(`licenseKey`/`appSecret`). Supports search (lead name, company name, or linked "
        "contact names), stage filtering, and pagination."
    ),
    responses={
        http_status.HTTP_200_OK: {"description": "Leads retrieved successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Not found"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
    },
)
@limiter.limit("200/minute")
async def external_list_leads(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    organization_id: str = Depends(get_organization_context),
    stage_id: str | None = Query(None, description="Filter by pipeline stage"),
    search: str | None = Query(
        None,
        description="Search by lead name, company name, or any linked contact name",
    ),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Page size"),
):
    """External list leads endpoint (Isometrik credential auth)."""
    actor_email = (
        getattr(request.state, "external_actor_email", None)
        or f"api@{name_to_email_domain_label(organization_id)}.com"
    )
    user_context = UserContext(
        user_id="00000000-0000-0000-0000-000000000000",
        email=actor_email,
        organization_id=organization_id,
    )
    service = LeadService(user_context=user_context, db_connection=db_connection)

    params = LeadsListQueryParams(
        mode=LeadsListMode.LIST,
        stage_id=stage_id,
        search=search,
        page=page,
        limit=page_size,
    )
    items, total, page_no = await service.list_leads(params)
    mapped = items or []
    return list_response(
        request=request,
        items=mapped,
        total=total or 0,
        page=page_no,
        page_size=page_size,
        message_key="leads.success.leads_retrieved",
        custom_code=CustomStatusCode.SUCCESS if mapped else CustomStatusCode.NO_CONTENT,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("external get lead")
@router.get(
    "/{lead_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Get lead details (external auth)",
    description=(
        "Fetch a single lead by ID for the organization resolved from Isometrik "
        "credentials (`licenseKey`/`appSecret`)."
    ),
    responses={
        http_status.HTTP_200_OK: {"description": "Lead retrieved successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Lead not found"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
    },
)
@limiter.limit("200/minute")
async def external_get_lead(
    request: Request,
    lead_id: str = Path(..., description="Lead ID"),
    db_connection: asyncpg.Connection = Depends(db_conn),
    organization_id: str = Depends(get_organization_context),
):
    """External get lead endpoint (Isometrik credential auth)."""
    actor_email = (
        getattr(request.state, "external_actor_email", None)
        or f"api@{name_to_email_domain_label(organization_id)}.com"
    )
    user_context = UserContext(
        user_id="00000000-0000-0000-0000-000000000000",
        email=actor_email,
        organization_id=organization_id,
    )
    service = LeadService(user_context=user_context, db_connection=db_connection)
    data = await service.get_lead(lead_id)
    return success_response(
        request=request,
        message_key="leads.success.lead_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=data,
    )


@handle_api_exceptions("external create lead")
@router.post(
    "",
    status_code=http_status.HTTP_201_CREATED,
    summary="Create a lead (external auth)",
    description=(
        "Create a new v2 lead using external auth (Isometrik credentials). Body matches "
        "``POST /leads``: company link via ``client_company_id``, person clients via "
        "``contacts``, required ``deal_type``, structured ``notes``, and FieldCell "
        "``custom_fields`` create rules."
    ),
    responses={
        http_status.HTTP_201_CREATED: {"description": "Lead created successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_404_NOT_FOUND: {
            "description": "Company/contact client, pipeline stage, or referenced user not found"
        },
        http_status.HTTP_409_CONFLICT: {"description": "A lead already exists for this client"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
    },
)
@limiter.limit("60/minute")
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
async def external_create_lead(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    organization_id: str = Depends(get_organization_context),
    body: CreateLeadRequest = Body(...),
):
    """External create lead endpoint (Isometrik credential auth)."""
    actor_email = (
        getattr(request.state, "external_actor_email", None)
        or f"api@{name_to_email_domain_label(organization_id)}.com"
    )
    user_context = UserContext(
        user_id=None,
        email=actor_email,
        organization_id=organization_id,
    )
    async with db_connection.transaction():
        service = LeadService(user_context=user_context, db_connection=db_connection)
        created = await service.create_lead(body, external=True)

    request.state.audit_table = "leads"
    request.state.audit_description = f"Created external lead for client: {body.client_company_id}"
    request.state.audit_risk_level = "medium"
    request.state.audit_user_context = {
        "user_id": "00000000-0000-0000-0000-000000000000",
        "user_email": actor_email,
        "organization_id": organization_id,
    }
    request.state.raw_audit_new_data = created
    return success_response(
        request=request,
        message_key="leads.success.lead_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data={"id": str(created.get("id"))} if isinstance(created, dict) else None,
    )


@handle_api_exceptions("external update lead")
@router.patch(
    "/{lead_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Update a lead (external auth)",
    description=(
        "Update a lead (PATCH semantics) using external auth (Isometrik credentials). "
        "Same rules as ``PATCH /leads/{lead_id}``: ``contacts`` and ``notes`` replace "
        "their arrays when set; ``custom_fields`` follow FieldCell PATCH rules."
    ),
    responses={
        http_status.HTTP_200_OK: {"description": "Lead updated successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Lead or related resource not found"},
        http_status.HTTP_409_CONFLICT: {"description": "Conflict"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
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
async def external_update_lead(
    request: Request,
    lead_id: str = Path(..., description="Lead ID"),
    db_connection: asyncpg.Connection = Depends(db_conn),
    organization_id: str = Depends(get_organization_context),
    body: UpdateLeadRequest = Body(...),
):
    """External update lead endpoint (Isometrik credential auth)."""
    actor_email = (
        getattr(request.state, "external_actor_email", None)
        or f"api@{name_to_email_domain_label(organization_id)}.com"
    )
    user_context = UserContext(
        user_id=None,
        email=actor_email,
        organization_id=organization_id,
    )
    async with db_connection.transaction():
        service = LeadService(user_context=user_context, db_connection=db_connection)
        previous, updated = await service.update_lead(lead_id=lead_id, body=body)
        resolved_id = (
            str(updated.get("id"))
            if isinstance(updated, dict) and updated.get("id") is not None
            else str(lead_id)
        )

    request.state.audit_table = "leads"
    request.state.audit_requested_id = lead_id
    request.state.audit_description = f"Updated external lead: {lead_id}"
    request.state.audit_risk_level = "medium"
    request.state.audit_user_context = {
        "user_id": "00000000-0000-0000-0000-000000000000",
        "user_email": actor_email,
        "organization_id": organization_id,
    }
    request.state.raw_audit_old_data = previous
    request.state.raw_audit_new_data = updated
    return success_response(
        request=request,
        message_key="leads.success.lead_updated",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data={"id": resolved_id},
    )


@handle_api_exceptions("external delete lead")
@router.delete(
    "/{lead_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Delete a lead (external auth)",
    description="Hard-delete a lead using external auth (Isometrik credentials).",
    responses={
        http_status.HTTP_200_OK: {"description": "Lead deleted successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Lead not found"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
    },
)
@limiter.limit("60/minute")
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
async def external_delete_lead(
    request: Request,
    lead_id: str = Path(..., description="Lead ID"),
    db_connection: asyncpg.Connection = Depends(db_conn),
    organization_id: str = Depends(get_organization_context),
):
    """External delete lead endpoint (Isometrik credential auth)."""
    actor_email = (
        getattr(request.state, "external_actor_email", None)
        or f"api@{name_to_email_domain_label(organization_id)}.com"
    )
    user_context = UserContext(
        user_id=None,
        email=actor_email,
        organization_id=organization_id,
    )
    async with db_connection.transaction():
        service = LeadService(user_context=user_context, db_connection=db_connection)
        deleted = await service.delete_lead(lead_id)

    request.state.audit_table = "leads"
    request.state.audit_requested_id = lead_id
    request.state.audit_description = f"Deleted external lead: {lead_id}"
    request.state.audit_risk_level = "high"
    request.state.audit_user_context = {
        "user_id": "00000000-0000-0000-0000-000000000000",
        "user_email": actor_email,
        "organization_id": organization_id,
    }
    request.state.raw_audit_old_data = deleted
    return success_response(
        request=request,
        message_key="leads.success.lead_deleted",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )
