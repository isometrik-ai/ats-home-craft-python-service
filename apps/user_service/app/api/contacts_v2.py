"""Contacts v2 API.

Resource-specific endpoints targeting the split tables (`contacts`, `companies`,
`contact_companies`, `contact_addresses`) with the operations defined in
`ADRs/clients_operations.md`.
"""

import asyncpg
from fastapi import APIRouter, BackgroundTasks, Body, Depends, Path, Query, Request
from fastapi import status as http_status
from supabase import AsyncClient

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn
from apps.user_service.app.dependencies.supabase import supabase_service
from apps.user_service.app.schemas.contacts_v2 import (
    ContactDetailsResponse,
    CreateContactRequest,
    ContactSummaryResponse,
    UpdateContactRequest,
)
from apps.user_service.app.schemas.enums import ClientEventType, ClientStatus, KafkaTopics
from apps.user_service.app.services.contacts_service_v2 import ContactsServiceV2
from apps.user_service.app.services.event_service import EventService
from apps.user_service.app.services.client_enrichment_service import ClientEnrichmentService
from apps.user_service.app.services.typesense_index_service_v2 import (
    delete_contact_background,
    index_contacts_background,
)
from apps.user_service.app.utils.common_utils import check_permissions, handle_api_exceptions
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import (
    CLIENTS_MANAGEMENT_CREATE,
    CLIENTS_MANAGEMENT_DELETE,
    CLIENTS_MANAGEMENT_EDIT,
    CLIENTS_MANAGEMENT_VIEW,
)
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/contacts", tags=["Contacts v2"])

CLIENT_KAFKA_TOPICS: list[KafkaTopics] = [KafkaTopics.CRM_EVENTS]

COMMON_ERROR_RESPONSES: dict[int | str, dict] = {
    401: {"description": "Unauthorized (missing/invalid JWT)."},
    403: {"description": "Forbidden (insufficient permissions)."},
    404: {"description": "Not found."},
    422: {"description": "Validation error."},
    429: {"description": "Too many requests (rate limited)."},
    500: {"description": "Internal server error."},
}


@handle_api_exceptions("create contact")
@router.post(
    "",
    status_code=http_status.HTTP_201_CREATED,
    summary="Create a contact",
    description=(
        "Creates a contact in the v2 split-table model. Depending on the payload, this can also "
        "link the contact to an existing company or create a new company by name and associate it."
        "Side effects:"
        "- Emits lifecycle events (Kafka topic: CRM events)"
        "- Schedules Typesense indexing for the contact"
        "- Schedules enrichment for the created/affected entities (if configured)"
    ),
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="pii",
    compliance_tags=[
        "gdpr",
        "pii",
        "soc2_audit",
        "audit_required",
    ],
    table_name="contacts",
    category="CLIENT",
)
async def create_contact(
    request: Request,
    background_tasks: BackgroundTasks,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    sb_client: AsyncClient = Depends(supabase_service),
    body: CreateContactRequest = Body(
        ...,
        description=(
            "Supports creating a standalone contact, linking to an existing company, "
            "or creating a new company by name and associating it."
        ),
    ),
):
    """Create a contact (v2).

    This endpoint is the primary “create” entry-point for contacts in the split-table model.
    It can optionally create/link a company association in the same request.

    Args:
        request: FastAPI request (used for audit log context).
        background_tasks: Schedules non-blocking side effects (events/indexing/enrichment).
        db_connection: PostgreSQL connection (request-scoped).
        current_user: Authenticated user claims extracted from JWT.
        sb_client: Supabase client (used by the service for auth-related operations, if needed).
        body: Contact create payload.

    Returns:
        Standard success response envelope (201 Created). The created entity identifiers are not
        returned directly in the response body; they are available in audit logs/events.

    Side effects:
        - Creates lifecycle events for created entities (best-effort publish via BackgroundTasks).
        - Indexes the created contact in Typesense (BackgroundTasks).
        - Triggers enrichment for the created contact and any created company (BackgroundTasks).
    """
    created_events: list[tuple[dict, str]] = []
    contact_id: str | None = None
    async with db_connection.transaction():
        user_context = await check_permissions(
            current_user=current_user,
            db_connection=db_connection,
            permission_codes=CLIENTS_MANAGEMENT_CREATE,
        )
        request.state.audit_table = "contacts"
        request.state.audit_description = "Created contact"
        request.state.audit_risk_level = "high"
        request.state.audit_user_context = {
            "user_id": user_context.user_id,
            "user_email": user_context.email,
            "organization_id": user_context.organization_id,
        }
        service = ContactsServiceV2(
            db_connection=db_connection,
            user_context=user_context,
            supabase_client=sb_client,
        )
        event_service = EventService(db_connection=db_connection)
        result = await service.create_contact(body)
        contact_id = result["contact_id"]
        request.state.audit_requested_id = str(contact_id)
        request.state.audit_description = f"Created contact: {contact_id}"
        request.state.raw_audit_old_data = result.get("old_data")
        request.state.raw_audit_new_data = result.get("new_data")
        for entity in result.get("created_entities") or []:
            entity_id = entity.get("entity_id")
            if not entity_id:
                continue
            evt = await event_service.create_lifecycle_event(
                event_type=ClientEventType.CREATED.value,
                aggregate_id=str(entity_id),
                organization_id=user_context.organization_id,
                actor_user_id=str(user_context.user_id) if user_context.user_id else None,
                payload={"module": "contacts_v2", "action": entity.get("action") or "create"},
                topics=CLIENT_KAFKA_TOPICS,
            )
            if evt is not None:
                created_events.append((evt, str(entity_id)))

    for evt, key in created_events:
        background_tasks.add_task(
            EventService.publish_event_background,
            event=evt,
            key=key,
            topics=CLIENT_KAFKA_TOPICS,
        )
    if contact_id is not None:
        background_tasks.add_task(
            index_contacts_background,
            [(contact_id, user_context.organization_id)],
        )
        # Enrichment for created contact and optionally created company.
        enrichment_service = ClientEnrichmentService.from_settings()
        for item in result.get("enrichment_targets") or []:
            background_tasks.add_task(
                enrichment_service.run_client_enrichment,
                client_id=item["client_id"],
                organization_id=item["organization_id"],
                client_type=item["client_type"],
                payload_data=item.get("payload_data") or {},
                entity_table=item.get("entity_table") or "clients",
            )

    return success_response(
        request=request,
        message_key="clients.success.client_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
    )


@handle_api_exceptions("list contacts")
@router.get(
    "",
    status_code=http_status.HTTP_200_OK,
    summary="List contacts (database)",
    description=(
        "Returns paginated contacts from PostgreSQL.\n\n"
        "Notes:\n"
        "- Use `search` for a lightweight name/email search.\n"
        "- Use `/contacts/search` for Typesense-backed full-text search."
    ),
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def list_contacts(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    search: str | None = Query(
        None,
        min_length=2,
        description="Optional search string. Typically matches common identifying fields.",
    ),
    status: ClientStatus | None = Query(
        None,
        description="Optional contact status filter.",
    ),
    page: int = Query(
        1,
        ge=1,
        description="1-based page number.",
    ),
    page_size: int = Query(
        20,
        ge=1,
        le=100,
        description="Number of items per page (max 100).",
    ),
):
    """List contacts from PostgreSQL with pagination (v2).

    Args:
        request: FastAPI request.
        db_connection: PostgreSQL connection (request-scoped).
        current_user: Authenticated user claims extracted from JWT.
        search: Optional lightweight search filter (min 2 chars).
        status: Optional status filter.
        page: 1-based page number.
        page_size: Items per page (max 100).

    Returns:
        Paginated list response envelope containing contact summary items and total count.
    """
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=CLIENTS_MANAGEMENT_VIEW,
    )
    service = ContactsServiceV2(db_connection=db_connection, user_context=user_context)
    result = await service.list_contacts(
        search=search,
        status=status.value if status else None,
        page=page,
        page_size=page_size,
    )
    items = [
        ContactSummaryResponse.model_validate(r).model_dump(exclude_none=True)
        for r in result["items"]
    ]
    total = int(result["total"])
    if not items:
        return list_response(
            request=request,
            items=[],
            total=0,
            page=page,
            page_size=page_size,
            message_key="success.no_data",
            custom_code=CustomStatusCode.NO_CONTENT,
            status_code=http_status.HTTP_200_OK,
        )
    return list_response(
        request=request,
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        message_key="clients.success.clients_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("search contacts")
@router.get(
    "/search",
    status_code=http_status.HTTP_200_OK,
    summary="Search contacts (Typesense)",
    description=(
        "Performs full-text search over contacts using Typesense."
        "This endpoint currently returns raw Typesense hits."
    ),
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def search_contacts(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    query: str = Query(
        ...,
        min_length=2,
        description="Search query (min 2 chars).",
    ),
    status: ClientStatus | None = Query(
        None,
        description="Optional contact status filter applied to the search.",
    ),
    page: int = Query(
        1,
        ge=1,
        description="1-based page number.",
    ),
    page_size: int = Query(
        20,
        ge=1,
        le=100,
        description="Number of hits per page (max 100).",
    ),
):
    """Search contacts using Typesense (v2).

    Args:
        request: FastAPI request.
        db_connection: PostgreSQL connection (request-scoped) used for permission checks.
        current_user: Authenticated user claims extracted from JWT.
        query: Full-text query (min 2 chars).
        status: Optional status filter applied to the search.
        page: 1-based page number.
        page_size: Hits per page (max 100).

    Returns:
        Paginated list response envelope containing raw Typesense hits and total count.

    Notes:
        This endpoint intentionally returns raw Typesense hits.
    """
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=CLIENTS_MANAGEMENT_VIEW,
    )
    service = ContactsServiceV2(db_connection=db_connection, user_context=user_context)
    result = await service.search_contacts(
        query=query,
        page=page,
        page_size=page_size,
        status=status.value if status else None,
    )
    items = result["items"]
    total = result["total"]
    if not items:
        return list_response(
            request=request,
            items=[],
            total=0,
            page=page,
            page_size=page_size,
            message_key="success.no_data",
            custom_code=CustomStatusCode.NO_CONTENT,
            status_code=http_status.HTTP_200_OK,
        )
    return list_response(
        request=request,
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        message_key="clients.success.clients_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("get contact details")
@router.get(
    "/{contact_id}",
    summary="Get contact details",
    description="Returns a single contact, including linked companies and addresses (v2).",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def get_contact_details(
    request: Request,
    contact_id: str = Path(
        ...,
        description="Contact identifier (UUID string).",
    ),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Get a single contact including addresses and linked companies (v2).

    Args:
        request: FastAPI request.
        contact_id: Contact identifier.
        db_connection: PostgreSQL connection (request-scoped).
        current_user: Authenticated user claims extracted from JWT.

    Returns:
        Success response envelope containing a contact details payload.
    """
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=CLIENTS_MANAGEMENT_VIEW,
    )
    service = ContactsServiceV2(db_connection=db_connection, user_context=user_context)
    details = await service.get_contact_details(contact_id=contact_id)
    details = ContactDetailsResponse.model_validate(details).model_dump(exclude_none=True)
    return success_response(
        request=request,
        message_key="clients.success.client_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=details,
    )


@handle_api_exceptions("update contact")
@router.patch(
    "/{contact_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Update a contact",
    description=(
        "Updates contact fields and related nested data (e.g., addresses). "
        "May also apply company association changes when `companies_update` is provided."
        "Side effects:"
        "- Emits an UPDATED lifecycle event"
        "- Schedules Typesense re-indexing for the contact"
    ),
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=[
        "gdpr",
        "pii",
        "soc2_audit",
        "audit_required",
    ],
    table_name="contacts",
    category="CLIENT",
)
async def update_contact(
    request: Request,
    background_tasks: BackgroundTasks,
    contact_id: str = Path(
        ...,
        description="Contact identifier (UUID string).",
    ),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    body: UpdateContactRequest = Body(
        ...,
        description="Partial update payload. Only provided fields are updated.",
    ),
):
    """Update a contact (fields + addresses + optional company association change) (v2).

    Args:
        request: FastAPI request (used for audit log context).
        background_tasks: Schedules non-blocking side effects (events/indexing).
        contact_id: Contact identifier.
        db_connection: PostgreSQL connection (request-scoped).
        current_user: Authenticated user claims extracted from JWT.
        body: Partial update payload. Only provided fields are updated.

    Returns:
        Success response envelope containing the service result payload.

    Side effects:
        - Emits an UPDATED lifecycle event (best-effort publish via BackgroundTasks).
        - Schedules Typesense re-indexing for the contact (BackgroundTasks).
    """
    update_event: dict | None = None
    async with db_connection.transaction():
        user_context = await check_permissions(
            current_user=current_user,
            db_connection=db_connection,
            permission_codes=CLIENTS_MANAGEMENT_EDIT,
        )
        service = ContactsServiceV2(db_connection=db_connection, user_context=user_context)
        event_service = EventService(db_connection=db_connection)
        request.state.audit_table = "contacts"
        request.state.audit_requested_id = contact_id
        request.state.audit_description = f"Updated contact: {contact_id}"
        request.state.audit_risk_level = "medium"
        request.state.audit_user_context = {
            "user_id": user_context.user_id,
            "user_email": user_context.email,
            "organization_id": user_context.organization_id,
        }
        result = await service.update_contact(contact_id=contact_id, body=body)
        changed_fields = list(body.model_dump(exclude_unset=True, exclude_none=True).keys())
        request.state.raw_audit_old_data = result.get("old_data")
        request.state.raw_audit_new_data = result.get("new_data")
        update_event = await event_service.create_lifecycle_event(
            event_type=ClientEventType.UPDATED.value,
            aggregate_id=contact_id,
            organization_id=user_context.organization_id,
            actor_user_id=str(user_context.user_id) if user_context.user_id else None,
            payload={"module": "contacts_v2", "action": "update", "changed_fields": changed_fields},
            topics=CLIENT_KAFKA_TOPICS,
        )

    ContactsServiceV2.schedule_contact_update_background_tasks(
        background_tasks=background_tasks,
        contact_id=contact_id,
        organization_id=user_context.organization_id,
        body=body,
        update_result=result if isinstance(result, dict) else None,
        update_event=update_event,
        event_key=contact_id,
        event_topics=CLIENT_KAFKA_TOPICS,
    )
    return success_response(
        request=request,
        message_key="clients.success.client_updated",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("enrich contact")
@router.post(
    "/{contact_id}/enrich",
    status_code=http_status.HTTP_200_OK,
    summary="Trigger contact enrichment",
    description=(
        "Triggers enrichment for a contact using the latest persisted data. "
        "This mirrors the legacy client enrichment trigger flow but is scoped to v2 contacts."
    ),
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("60/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "soc2_audit", "audit_required"],
    table_name="contacts",
    category="CLIENT",
)
async def enrich_contact(
    request: Request,
    background_tasks: BackgroundTasks,
    contact_id: str = Path(..., description="Contact identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Trigger enrichment for a v2 contact (best-effort async)."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=CLIENTS_MANAGEMENT_EDIT,
    )
    request.state.audit_table = "contacts"
    request.state.audit_requested_id = contact_id
    request.state.audit_description = f"Triggered enrichment for contact: {contact_id}"
    request.state.audit_risk_level = "medium"
    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }
    background_tasks.add_task(
        ContactsServiceV2.trigger_enrichment_background,
        contact_id,
        user_context.organization_id,
    )
    return success_response(
        request=request,
        message_key="clients.success.client_updated",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("delete contact")
@router.delete(
    "/{contact_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Delete a contact (soft delete)",
    description=(
        "Soft-deletes a contact.\n\n"
        "Side effects:\n"
        "- Emits a DELETED lifecycle event\n"
        "- Schedules Typesense de-indexing for the contact"
    ),
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="pii",
    compliance_tags=[
        "gdpr",
        "pii",
        "soc2_audit",
        "audit_required",
    ],
    table_name="contacts",
    category="CLIENT",
)
async def delete_contact(
    request: Request,
    background_tasks: BackgroundTasks,
    contact_id: str = Path(
        ...,
        description="Contact identifier (UUID string).",
    ),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Soft-delete a contact (v2).

    Args:
        request: FastAPI request (used for audit log context).
        background_tasks: Schedules non-blocking side effects (events/de-indexing).
        contact_id: Contact identifier.
        db_connection: PostgreSQL connection (request-scoped).
        current_user: Authenticated user claims extracted from JWT.

    Returns:
        Standard success response envelope (200 OK).

    Side effects:
        - Emits a DELETED lifecycle event (best-effort publish via BackgroundTasks).
        - Schedules Typesense de-indexing for the contact (BackgroundTasks).
    """
    event: dict | None = None
    async with db_connection.transaction():
        user_context = await check_permissions(
            current_user=current_user,
            db_connection=db_connection,
            permission_codes=CLIENTS_MANAGEMENT_DELETE,
        )
        service = ContactsServiceV2(db_connection=db_connection, user_context=user_context)
        event_service = EventService(db_connection=db_connection)
        request.state.audit_table = "contacts"
        request.state.audit_requested_id = contact_id
        request.state.audit_description = f"Deleted contact: {contact_id}"
        request.state.audit_risk_level = "high"
        request.state.audit_user_context = {
            "user_id": user_context.user_id,
            "user_email": user_context.email,
            "organization_id": user_context.organization_id,
        }
        deleted = await service.soft_delete_contact(contact_id=contact_id)
        request.state.raw_audit_old_data = deleted.get("old_data")
        request.state.raw_audit_new_data = deleted.get("new_data")
        event = await event_service.create_lifecycle_event(
            event_type=ClientEventType.DELETED.value,
            aggregate_id=contact_id,
            organization_id=user_context.organization_id,
            actor_user_id=str(user_context.user_id) if user_context.user_id else None,
            payload={"module": "contacts_v2", "action": "delete"},
            topics=CLIENT_KAFKA_TOPICS,
        )

    if event is not None:
        background_tasks.add_task(
            EventService.publish_event_background,
            event=event,
            key=contact_id,
            topics=CLIENT_KAFKA_TOPICS,
        )
    background_tasks.add_task(delete_contact_background, contact_id)
    return success_response(
        request=request,
        message_key="clients.success.client_deleted",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )
