"""Leads API module."""

import asyncpg
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Depends,
    Path,
    Query,
    Request,
)
from fastapi import status as http_status
from supabase import AsyncClient

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn
from apps.user_service.app.dependencies.supabase import supabase_service
from apps.user_service.app.schemas.enums import (
    KafkaTopics,
    LeadEventType,
    LeadsListMode,
)
from apps.user_service.app.schemas.leads import (
    CreateLeadRequest,
    LeadsListQueryParams,
    ListLeadsRequest,
    UpdateLeadRequest,
)
from apps.user_service.app.services.activity_service import ActivityService
from apps.user_service.app.services.event_service import EventService
from apps.user_service.app.services.external_leads_service import ExternalLeadsService
from apps.user_service.app.services.lead_service import LeadService
from apps.user_service.app.utils.common_utils import (
    check_permissions,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import check_user_access_async, get_user_from_auth
from libs.shared_utils.common_query import (
    LEADS_MANAGEMENT_CREATE,
    LEADS_MANAGEMENT_DELETE,
    LEADS_MANAGEMENT_EDIT,
    LEADS_MANAGEMENT_VIEW,
    LEADS_MANAGEMENT_VIEW_SYSTEM,
)
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/leads", tags=["Leads"])
LEAD_KAFKA_TOPICS: list[KafkaTopics] = [KafkaTopics.CRM_EVENTS]
CLIENT_KAFKA_TOPICS: list[KafkaTopics] = [KafkaTopics.CRM_EVENTS]


@handle_api_exceptions("create lead")
@router.post(
    "",
    status_code=http_status.HTTP_201_CREATED,
    description="Create a new lead with optional links to contacts and a single company",
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
    background_tasks: BackgroundTasks,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    sb_client: AsyncClient = Depends(supabase_service),
    body: CreateLeadRequest = Body(...),
):
    """Create a lead for the authenticated organization."""
    async with db_connection.transaction():
        user_context = await check_permissions(
            current_user=current_user,
            db_connection=db_connection,
            permission_codes=LEADS_MANAGEMENT_CREATE,
        )
        organization_id = user_context.organization_id
        actor_user_id = str(user_context.user_id) if user_context.user_id else None

        service = ExternalLeadsService(
            db_connection=db_connection,
            user_context=user_context,
            supabase_client=sb_client,
            client_kafka_topics=CLIENT_KAFKA_TOPICS,
            lead_kafka_topics=LEAD_KAFKA_TOPICS,
            organization_id=organization_id,
        )
        result = await service.create_lead_with_optional_contact(
            lead=body.to_lead_payload(),
            contact=body.create_contact,
            lead_contact_label=body.created_contact_label,
            external=False,
            require_linked_contact=False,
            actor_user_id=actor_user_id,
        )
        ExternalLeadsService.apply_create_audit_state(
            request,
            result=result,
            user_context=user_context,
        )

    ExternalLeadsService.schedule_create_post_commit(
        background_tasks,
        result=result,
        organization_id=organization_id,
        lead_kafka_topics=LEAD_KAFKA_TOPICS,
    )

    return success_response(
        request=request,
        message_key="leads.success.lead_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
    )


@handle_api_exceptions("get lead activity")
@router.get(
    "/activity/{lead_id}/",
    status_code=http_status.HTTP_200_OK,
    description=(
        "Activity feed for a lead. `page` / `page_size` paginate (newest first). "
        "`data` contains flattened lines (often one per changed field). `total` and `total_pages` "
        "refer to audit rows; `len(data)` may be larger than `page_size`."
    ),
    summary="Get lead activity",
    responses={
        http_status.HTTP_200_OK: {"description": "Lead activity retrieved successfully"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Lead not found"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
    },
)
@limiter.limit("100/minute")
async def get_lead_activity(
    request: Request,
    lead_id: str = Path(..., description="Lead ID"),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Audit log rows per page"),
):
    """Get activity for a lead (offset pagination)."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=LEADS_MANAGEMENT_VIEW,
    )

    can_view_system_leads = await check_user_access_async(
        permission_code=[LEADS_MANAGEMENT_VIEW_SYSTEM],
        user_id=user_context.user_id,
        organization_id=user_context.organization_id,
        db_connection=db_connection,
    )
    effective_owner_id = None if can_view_system_leads else user_context.user_id

    # Ensure lead exists (and org-scoped) before returning activity.
    lead_service = LeadService(user_context=user_context, db_connection=db_connection)
    await lead_service.get_lead(
        lead_id,
        owner_id=effective_owner_id,
    )

    activity_service = ActivityService(user_context=user_context, db_connection=db_connection)
    items, total = await activity_service.get_lead_activity(
        lead_id=lead_id,
        limit=page_size,
        offset=(page - 1) * page_size,
    )

    if not items:
        # No activity on this lead vs empty page
        # (e.g. page past total_pages): same pagination fields.
        if total == 0:
            return list_response(
                request=request,
                items=[],
                total=total,
                message_key="success.no_data",
                custom_code=CustomStatusCode.NO_CONTENT,
                status_code=http_status.HTTP_200_OK,
                page=page,
                page_size=page_size,
            )
        return list_response(
            request=request,
            items=[],
            total=total,
            message_key="success.retrieved",
            custom_code=CustomStatusCode.SUCCESS,
            status_code=http_status.HTTP_200_OK,
            page=page,
            page_size=page_size,
        )

    return list_response(
        request=request,
        items=items,
        total=total,
        message_key="success.retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        page=page,
        page_size=page_size,
    )


@handle_api_exceptions("list leads")
@router.post(
    "/list",
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
    body: ListLeadsRequest = Body(...),
):
    """List leads for the authenticated organization."""
    params = LeadsListQueryParams.model_validate(body.model_dump(exclude={"dropdown_filters"}))
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=LEADS_MANAGEMENT_VIEW,
    )

    can_view_system_leads = await check_user_access_async(
        permission_code=[LEADS_MANAGEMENT_VIEW_SYSTEM],
        user_id=user_context.user_id,
        organization_id=user_context.organization_id,
        db_connection=db_connection,
    )
    effective_owner_id = body.owner_id if can_view_system_leads else user_context.user_id

    lead_service = LeadService(
        user_context=user_context,
        db_connection=db_connection,
    )
    dropdown_filters = [f.model_dump(mode="json") for f in body.dropdown_filters]
    result = await lead_service.list_leads(
        params,
        owner_id=effective_owner_id,
        dropdown_filters=dropdown_filters,
    )

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

    can_view_system_leads = await check_user_access_async(
        permission_code=[LEADS_MANAGEMENT_VIEW_SYSTEM],
        user_id=user_context.user_id,
        organization_id=user_context.organization_id,
        db_connection=db_connection,
    )
    effective_owner_id = None if can_view_system_leads else user_context.user_id

    lead_service = LeadService(
        user_context=user_context,
        db_connection=db_connection,
    )
    data = await lead_service.get_lead(
        lead_id,
        owner_id=effective_owner_id,
    )

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
    background_tasks: BackgroundTasks,
    lead_id: str = Path(..., description="Lead ID"),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    body: UpdateLeadRequest = Body(...),
):
    """Update a lead for the authenticated organization."""
    update_event: dict | None = None
    async with db_connection.transaction():
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
        event_service = EventService(db_connection=db_connection)
        previous, updated = await lead_service.update_lead(lead_id=lead_id, body=body)
        update_event = await event_service.create_lifecycle_event(
            event_type=LeadEventType.UPDATED.value,
            aggregate_id=lead_id,
            organization_id=user_context.organization_id,
            actor_user_id=str(user_context.user_id) if user_context.user_id else None,
            payload={
                "module": "leads",
                "action": "update",
            },
            topics=LEAD_KAFKA_TOPICS,
        )
        # Normalize audit snapshots so association keys are always present and stable.
        # (No extra DB round-trips; we only standardize the payload shape for diffing.)
        request.state.raw_audit_old_data = LeadService._normalize_lead_audit_snapshot(previous)
        request.state.raw_audit_new_data = LeadService._normalize_lead_audit_snapshot(updated)

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
    )


@handle_api_exceptions("delete lead")
@router.delete(
    "/{lead_id}",
    status_code=http_status.HTTP_200_OK,
    description="Hard-delete a lead (linked contact and company records are not deleted)",
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
    background_tasks: BackgroundTasks,
    lead_id: str = Path(..., description="Lead ID"),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Hard-delete a lead for the authenticated organization."""
    delete_event: dict | None = None
    async with db_connection.transaction():
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
        event_service = EventService(db_connection=db_connection)
        deleted = await lead_service.delete_lead(lead_id)
        delete_event = await event_service.create_lifecycle_event(
            event_type=LeadEventType.DELETED.value,
            aggregate_id=lead_id,
            organization_id=user_context.organization_id,
            actor_user_id=str(user_context.user_id) if user_context.user_id else None,
            payload={"module": "leads", "action": "delete"},
            topics=LEAD_KAFKA_TOPICS,
        )
        # Normalize audit snapshot so association keys are always present.
        request.state.raw_audit_old_data = LeadService._normalize_lead_audit_snapshot(deleted)

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
