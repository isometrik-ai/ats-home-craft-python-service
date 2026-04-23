"""Contacts API.

Resource-specific endpoints targeting the split tables (`contacts`, `companies`,
`contact_companies`, `contact_addresses`) with the operations defined in
`ADRs/clients_operations.md`.
"""

from typing import Any

import asyncpg
from fastapi import APIRouter, BackgroundTasks, Body, Depends, Path, Query, Request
from fastapi import status as http_status
from supabase import AsyncClient

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn
from apps.user_service.app.dependencies.supabase import supabase_service
from apps.user_service.app.schemas.contacts import (
    ContactDetailsResponse,
    ContactSummaryResponse,
    CreateContactRequest,
    ListContactsRequest,
    UpdateContactRequest,
)
from apps.user_service.app.schemas.enums import (
    ClientEventType,
    ClientStatus,
    KafkaTopics,
)
from apps.user_service.app.services.activity_service import ActivityService
from apps.user_service.app.services.contacts_service import ContactsService
from apps.user_service.app.services.event_service import EventService
from apps.user_service.app.services.typesense_index_service import (
    delete_contact_background,
)
from apps.user_service.app.utils.common_utils import (
    check_permissions,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import (
    CLIENTS_MANAGEMENT_CREATE,
    CLIENTS_MANAGEMENT_DELETE,
    CLIENTS_MANAGEMENT_EDIT,
    CLIENTS_MANAGEMENT_VIEW,
)
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/contacts", tags=["Contacts"])

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
        "Creates a contact in the split-table model. Depending on the payload, this can also "
        "link the contact to an existing company or create a new company by name and associate it."
        "Side effects:"
        "- Emits lifecycle events (Kafka topic: CRM events)"
        "- Schedules Typesense indexing for the contact "
        "(and for a company if one was created inline)"
        "- Schedules enrichment for the created/affected entities"
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
    """Create a contact.

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
        - Indexes the created contact in Typesense (BackgroundTasks), and a new company in Typesense
          when one was created in the same request (BackgroundTasks).
        - Triggers enrichment for the created contact and any created company (BackgroundTasks).
    """
    created_events: list[tuple[dict, str]] = []
    contact_id: str | None = None
    user_context = None
    result: dict[str, Any] = {}
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
        service = ContactsService(
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
        created_events = await ContactsService.create_lifecycle_events_for_created_entities(
            event_service=event_service,
            created_entities=result.get("created_entities"),
            organization_id=user_context.organization_id,
            actor_user_id=str(user_context.user_id) if user_context.user_id else None,
        )

    ContactsService.schedule_lifecycle_event_publishes(
        background_tasks=background_tasks,
        created_events=created_events,
    )
    if contact_id is not None and user_context is not None:
        ContactsService.schedule_typesense_indexing_for_created_entities(
            background_tasks=background_tasks,
            created_entities=result.get("created_entities"),
            organization_id=user_context.organization_id,
        )
        ContactsService.schedule_enrichment(
            background_tasks=background_tasks,
            enrichment_targets=result.get("enrichment_targets"),
        )

    return success_response(
        request=request,
        message_key="contacts.success.contact_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
    )


@handle_api_exceptions("list contacts")
@router.post(
    "/list",
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
    body: ListContactsRequest = Body(...),
):
    """List contacts from PostgreSQL with pagination.

    Args:
        request: FastAPI request.
        db_connection: PostgreSQL connection (request-scoped).
        current_user: Authenticated user claims extracted from JWT.
        body: JSON body with filters and pagination.

    Returns:
        Paginated list response envelope containing contact summary items and total count.
    """
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=CLIENTS_MANAGEMENT_VIEW,
    )
    service = ContactsService(db_connection=db_connection, user_context=user_context)

    dropdown_filters = [f.model_dump(mode="json") for f in body.dropdown_filters]
    result = await service.list_contacts(
        search=body.search,
        status=body.status.value if body.status else None,
        dropdown_filters=dropdown_filters,
        page=body.page,
        page_size=body.page_size,
    )
    items = [
        ContactSummaryResponse.model_validate(summary_row).model_dump(exclude_none=True)
        for summary_row in result["items"]
    ]
    total = int(result["total"])
    if not items:
        return list_response(
            request=request,
            items=[],
            total=0,
            page=body.page,
            page_size=body.page_size,
            message_key="success.no_data",
            custom_code=CustomStatusCode.NO_CONTENT,
            status_code=http_status.HTTP_200_OK,
        )
    return list_response(
        request=request,
        items=items,
        total=total,
        page=body.page,
        page_size=body.page_size,
        message_key="contacts.success.contacts_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("get contact activity")
@router.get(
    "/activity/{contact_id}/",
    status_code=http_status.HTTP_200_OK,
    description=(
        "Activity feed for a contact. `page` / `page_size` paginate (newest first). "
        "`data` contains flattened lines (often one per changed field). `total` and `total_pages` "
        "refer to audit rows; `len(data)` may be larger than `page_size`."
    ),
    summary="Get contact activity",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def get_contact_activity(
    request: Request,
    contact_id: str = Path(..., description="Contact identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Audit log rows per page"),
):
    """Get activity for a contact (offset pagination)."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=CLIENTS_MANAGEMENT_VIEW,
    )

    # Ensure contact exists (and org-scoped) before returning activity.
    service = ContactsService(db_connection=db_connection, user_context=user_context)
    await service.get_contact_details(contact_id=contact_id)

    activity_service = ActivityService(user_context=user_context, db_connection=db_connection)
    items, total = await activity_service.get_contact_activity(
        contact_id=contact_id,
        limit=page_size,
        offset=(page - 1) * page_size,
    )

    if not items:
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
    """Search contacts using Typesense.

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
    service = ContactsService(db_connection=db_connection, user_context=user_context)
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
        message_key="contacts.success.contacts_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("get contact details")
@router.get(
    "/{contact_id}",
    summary="Get contact details",
    description="Returns a single contact, including linked companies and addresses.",
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
    """Get a single contact including addresses and linked companies.

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
    service = ContactsService(db_connection=db_connection, user_context=user_context)
    details = await service.get_contact_details(contact_id=contact_id)
    details = ContactDetailsResponse.model_validate(details).model_dump(exclude_none=True)
    return success_response(
        request=request,
        message_key="contacts.success.contact_retrieved",
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
        "May also apply company association changes when `company_association` is provided."
        "Side effects:"
        "- Emits lifecycle events for the contact and each company touched by `company_association`"
        "- Schedules Typesense re-indexing for the contact and those companies"
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
    """Update a contact (fields + addresses + optional company association change).

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
        - Emits lifecycle events for the contact and any companies touched by `company_association`
          (best-effort publish via BackgroundTasks).
        - Schedules Typesense re-indexing for the contact and those companies (BackgroundTasks).
    """
    update_event: dict | None = None
    related_lifecycle_events: list[tuple[dict[str, Any], str]] = []
    async with db_connection.transaction():
        user_context = await check_permissions(
            current_user=current_user,
            db_connection=db_connection,
            permission_codes=CLIENTS_MANAGEMENT_EDIT,
        )
        service = ContactsService(db_connection=db_connection, user_context=user_context)
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
            payload={"module": "contacts", "action": "update", "changed_fields": changed_fields},
            topics=CLIENT_KAFKA_TOPICS,
        )

        companies_delta = (result.get("companies_delta") or {}) if isinstance(result, dict) else {}
        raw_affected = companies_delta.get("affected_company_ids") or []
        affected_company_ids = list(dict.fromkeys(str(cid) for cid in raw_affected))
        created_cid = companies_delta.get("created_company_id")
        created_cid_s = str(created_cid) if created_cid else None
        if affected_company_ids:
            actor = str(user_context.user_id) if user_context.user_id else None
            org_id = user_context.organization_id
            company_event_items = [
                {
                    "event_type": (
                        ClientEventType.CREATED.value
                        if created_cid_s is not None and cid_s == created_cid_s
                        else ClientEventType.UPDATED.value
                    ),
                    "aggregate_id": cid_s,
                    "organization_id": org_id,
                    "actor_user_id": actor,
                    "payload": {
                        "module": "contacts",
                        "action": (
                            "company_created_with_contact"
                            if created_cid_s is not None and cid_s == created_cid_s
                            else "company_association_changed"
                        ),
                        "contact_id": contact_id,
                    },
                }
                for cid_s in affected_company_ids
            ]
            company_events = await event_service.create_lifecycle_events(
                items=company_event_items,
                topics=CLIENT_KAFKA_TOPICS,
            )
            related_lifecycle_events.extend(
                (event_payload, event_payload["aggregate_id"]) for event_payload in company_events
            )

    ContactsService.schedule_contact_update_background_tasks(
        background_tasks=background_tasks,
        contact_id=contact_id,
        organization_id=user_context.organization_id,
        body=body,
        update_result=result if isinstance(result, dict) else None,
        update_event=update_event,
        event_key=contact_id,
        event_topics=CLIENT_KAFKA_TOPICS,
        related_lifecycle_events=related_lifecycle_events,
    )
    return success_response(
        request=request,
        message_key="contacts.success.contact_updated",
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
        "This mirrors the legacy client enrichment trigger flow but is scoped to contacts."
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
    """Trigger enrichment for a contact (best-effort async).

    Args:
        request: FastAPI request (audit context).
        background_tasks: Schedules enrichment work after the response.
        contact_id: Contact identifier.
        db_connection: PostgreSQL connection (request-scoped).
        current_user: Authenticated user claims from JWT.

    Returns:
        Success response envelope when the enrichment task is queued.
    """
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
        ContactsService.trigger_enrichment_background,
        contact_id,
        user_context.organization_id,
    )
    return success_response(
        request=request,
        message_key="contacts.success.contact_enrichment_requested",
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
    """Soft-delete a contact.

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
        service = ContactsService(db_connection=db_connection, user_context=user_context)
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
            payload={"module": "contacts", "action": "delete"},
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
        message_key="contacts.success.contact_deleted",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )
