"""External Leads API.

These endpoints are intended for external integrations (partners, embedded apps)
that do not authenticate with our JWT bearer token. Instead, the caller
authenticates via Isometrik credential decode using headers:

- ``licenseKey``
- ``appSecret``

The decoded ``projectId`` is mapped to our internal ``organization_id`` and all
operations are scoped to that organization.
"""

from typing import Any

import asyncpg
from fastapi import APIRouter, BackgroundTasks, Body, Depends, Path, Query, Request
from fastapi import status as http_status
from supabase import AsyncClient

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn
from apps.user_service.app.dependencies.external_auth import get_organization_context
from apps.user_service.app.dependencies.supabase import supabase_service
from apps.user_service.app.schemas.enums import (
    KafkaTopics,
    LeadEventType,
    LeadsListMode,
)
from apps.user_service.app.schemas.external_leads import ExternalCreateLeadRequest
from apps.user_service.app.schemas.leads import (
    CreateLeadRequest,
    LeadsListQueryParams,
    UpdateLeadRequest,
)
from apps.user_service.app.services.client_enrichment_service import (
    ClientEnrichmentService,
)
from apps.user_service.app.services.event_service import EventService
from apps.user_service.app.services.external_leads_service import ExternalLeadsService
from apps.user_service.app.services.lead_service import LeadService
from apps.user_service.app.services.typesense_index_service import (
    index_companies_background,
    index_contacts_background,
)
from apps.user_service.app.utils.common_utils import (
    UserContext,
    handle_api_exceptions,
    name_to_email_domain_label,
)
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/integrations/leads", tags=["Leads (External)"])
LEAD_KAFKA_TOPICS: list[KafkaTopics] = [KafkaTopics.CRM_EVENTS]

CLIENT_KAFKA_TOPICS: list[KafkaTopics] = [KafkaTopics.CRM_EVENTS]


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
    background_tasks: BackgroundTasks,
    db_connection: asyncpg.Connection = Depends(db_conn),
    sb_client: AsyncClient = Depends(supabase_service),
    organization_id: str = Depends(get_organization_context),
    body: ExternalCreateLeadRequest = Body(...),
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
    created_contact_id: str | None = None
    created_company_id: str | None = None
    contact_created_events: list[tuple[dict, str]] = []
    create_event: dict | None = None
    event_key: str | None = None
    contact_result: dict[str, Any] | None = None
    lead_payload: CreateLeadRequest | None = None
    created: dict[str, Any] | None = None
    async with db_connection.transaction():
        external_service = ExternalLeadsService(
            db_connection=db_connection,
            user_context=user_context,
            supabase_client=sb_client,
            client_kafka_topics=CLIENT_KAFKA_TOPICS,
            lead_kafka_topics=LEAD_KAFKA_TOPICS,
            organization_id=organization_id,
        )

        internal_lead = body.lead.model_copy(deep=True)
        result = await external_service.create_lead_with_optional_contact(
            lead=internal_lead,
            contact=body.create_contact,
            lead_contact_label=body.created_contact_label,
        )
        created = result.created
        lead_payload = result.lead_payload
        created_contact_id = result.created_contact_id
        created_company_id = result.created_company_id
        contact_created_events = result.contact_created_events
        create_event = result.lead_created_event
        event_key = result.lead_event_key
        contact_result = result.contact_result

        created_id = str(created.get("id", "")) if isinstance(created, dict) else ""

        request.state.audit_table = "leads"
        # Ensure CREATE audit logs can be linked back to this lead in the activity feed.
        request.state.audit_requested_id = created_id
        request.state.audit_description = (
            f"Created lead: {lead_payload.name!r}"
            if lead_payload is not None
            else "Created lead"
            if created_contact_id is None
            else (
                f"Created lead with new contact: {lead_payload.name!r}"
                if lead_payload is not None
                else "Created lead with new contact"
            )
        )
        request.state.audit_risk_level = "high" if created_contact_id is not None else "medium"
        request.state.audit_user_context = {
            "user_id": "00000000-0000-0000-0000-000000000000",
            "user_email": actor_email,
            "organization_id": organization_id,
        }
        # Normalize audit snapshot so association keys are always present.
        request.state.raw_audit_new_data = (
            LeadService._normalize_lead_audit_snapshot(created)
            if isinstance(created, dict)
            else created
        )
        if created_contact_id is not None:
            # Attach created entities to the audit snapshot for easier tracing.
            request.state.raw_audit_new_data = {
                "lead": request.state.raw_audit_new_data,
                "created_contact_id": created_contact_id,
                "created_company_id": created_company_id,
            }

    # Publish contact lifecycle events after commit.
    for lifecycle_event, event_publish_key in contact_created_events:
        background_tasks.add_task(
            EventService.publish_event_background,
            event=lifecycle_event,
            key=event_publish_key,
            topics=CLIENT_KAFKA_TOPICS,
        )

    if create_event is not None and event_key is not None:
        background_tasks.add_task(
            EventService.publish_event_background,
            event=create_event,
            key=event_key,
            topics=LEAD_KAFKA_TOPICS,
        )

    # Typesense indexing + enrichment (best-effort) after commit for newly created contact/company.
    if created_contact_id:
        background_tasks.add_task(
            index_contacts_background,
            [(created_contact_id, organization_id)],
        )
    if created_company_id:
        background_tasks.add_task(
            index_companies_background,
            [(created_company_id, organization_id)],
        )
    if contact_result is not None:
        enrichment_service = ClientEnrichmentService.from_settings()
        for item in (contact_result or {}).get("enrichment_targets") or []:
            background_tasks.add_task(
                enrichment_service.run_client_enrichment,
                client_id=item["client_id"],
                organization_id=item["organization_id"],
                client_type=item["client_type"],
                payload_data=item.get("payload_data") or {},
                entity_table=item.get("entity_table") or "clients",
                skip_company_logo=bool(item.get("skip_company_logo")),
            )

    response_data: dict[str, Any] = (
        {"lead_id": str(created.get("id"))} if isinstance(created, dict) else {}
    )

    if created_contact_id is not None:
        response_data["contact_id"] = created_contact_id
    if created_company_id is not None:
        response_data["company_id"] = created_company_id
    return success_response(
        request=request,
        message_key="leads.success.lead_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=response_data or None,
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
    background_tasks: BackgroundTasks,
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
    update_event: dict | None = None
    async with db_connection.transaction():
        service = LeadService(user_context=user_context, db_connection=db_connection)
        event_service = EventService(db_connection=db_connection)
        previous, updated = await service.update_lead(lead_id=lead_id, body=body)
        resolved_id = (
            str(updated.get("id"))
            if isinstance(updated, dict) and updated.get("id") is not None
            else str(lead_id)
        )

        request.state.audit_table = "leads"
        request.state.audit_requested_id = lead_id
        request.state.audit_description = f"Updated lead: {lead_id}"
        request.state.audit_risk_level = "medium"
        request.state.audit_user_context = {
            "user_id": "00000000-0000-0000-0000-000000000000",
            "user_email": actor_email,
            "organization_id": organization_id,
        }
        # Normalize audit snapshots so association keys are always present and stable.
        request.state.raw_audit_old_data = (
            LeadService._normalize_lead_audit_snapshot(previous)
            if isinstance(previous, dict)
            else previous
        )
        request.state.raw_audit_new_data = (
            LeadService._normalize_lead_audit_snapshot(updated)
            if isinstance(updated, dict)
            else updated
        )

        update_event = await event_service.create_lifecycle_event(
            event_type=LeadEventType.UPDATED.value,
            aggregate_id=lead_id,
            organization_id=organization_id,
            actor_user_id=None,
            payload={"module": "leads", "action": "update"},
            topics=LEAD_KAFKA_TOPICS,
        )

    if update_event is not None:
        background_tasks.add_task(
            EventService.publish_event_background,
            event=update_event,
            key=lead_id,
            topics=LEAD_KAFKA_TOPICS,
        )
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
    background_tasks: BackgroundTasks,
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
    delete_event: dict | None = None
    async with db_connection.transaction():
        service = LeadService(user_context=user_context, db_connection=db_connection)
        event_service = EventService(db_connection=db_connection)
        deleted = await service.delete_lead(lead_id)

        request.state.audit_table = "leads"
        request.state.audit_requested_id = lead_id
        request.state.audit_description = f"Deleted lead: {lead_id}"
        request.state.audit_risk_level = "high"
        request.state.audit_user_context = {
            "user_id": "00000000-0000-0000-0000-000000000000",
            "user_email": actor_email,
            "organization_id": organization_id,
        }
        # Normalize audit snapshot so association keys are always present.
        request.state.raw_audit_old_data = (
            LeadService._normalize_lead_audit_snapshot(deleted)
            if isinstance(deleted, dict)
            else deleted
        )

        delete_event = await event_service.create_lifecycle_event(
            event_type=LeadEventType.DELETED.value,
            aggregate_id=lead_id,
            organization_id=organization_id,
            actor_user_id=None,
            payload={"module": "leads", "action": "delete"},
            topics=LEAD_KAFKA_TOPICS,
        )

    if delete_event is not None:
        background_tasks.add_task(
            EventService.publish_event_background,
            event=delete_event,
            key=lead_id,
            topics=LEAD_KAFKA_TOPICS,
        )
    return success_response(
        request=request,
        message_key="leads.success.lead_deleted",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )
