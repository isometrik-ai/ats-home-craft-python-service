"""Clients Management API Module
This module provides CRUD operations for client management.
All endpoints include proper authentication, validation, and database operations.
"""

import asyncpg
from fastapi import APIRouter, BackgroundTasks, Body, Depends, Path, Query, Request
from fastapi import status as http_status
from supabase import AsyncClient

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.dependencies.supabase import supabase_service
from apps.user_service.app.schemas.clients import (
    ClientDetailsResponse,
    CreateClientFromUserRequest,
    CreateClientRequest,
    UpdateClientRequest,
)
from apps.user_service.app.schemas.enums import (
    ClientEventType,
    ClientStatus,
    ClientType,
    KafkaTopics,
)
from apps.user_service.app.services.client_service import (
    ClientEnrichmentService,
    ClientService,
)
from apps.user_service.app.services.event_service import EventService
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
from libs.shared_utils.logger import get_logger
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/clients", tags=["Clients Management"])

logger = get_logger("clients-api")

CLIENT_KAFKA_TOPICS: list[KafkaTopics] = [KafkaTopics.CRM_EVENTS]


@handle_api_exceptions("create client from user")
@router.post(
    "/from-auth",
    status_code=http_status.HTTP_201_CREATED,
    description="Create a client from user ID",
    summary="Create a client from user ID",
    responses={
        http_status.HTTP_201_CREATED: {"description": "Client created successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_404_NOT_FOUND: {"description": "User or organization not found"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_409_CONFLICT: {
            "description": "User is already a client, or user event is missing/not pending"
        },
    },
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="pii",
    compliance_tags=[
        "gdpr",  # Client creation involves personal information
        "pii",  # Client data contains personally identifiable information
        "soc2_audit",  # Client management is critical for SOC2 compliance
        "audit_required",  # Client creation requires audit trail
    ],
    table_name="clients",
    category="CLIENT",
)
async def create_client_from_user(
    request: Request,
    background_tasks: BackgroundTasks,
    db_connection: asyncpg.Connection = Depends(db_conn),
    body: CreateClientFromUserRequest = Body(...),
):
    """Create a client and client_user from user ID.

    This endpoint creates:
    1. A client record with client_type='person' and mandatory fields
    2. A client_user record linking the user to the client
    3. Sends a creation email to the user

    Args:
        request: FastAPI request object
        background_tasks: FastAPI background task manager
        db_connection: Database connection
        body: Request body containing user_id and organization_id

    Returns:
        Response with status code 201 and no body
    """
    # Set audit context for client creation
    request.state.audit_table = "clients"
    request.state.audit_description = f"Created client from user: {body.user_id}"
    request.state.audit_risk_level = "high"
    request.state.audit_user_context = {
        "user_id": body.user_id,
        "organization_id": body.organization_id,
    }

    event: dict | None = None
    async with db_connection.transaction():
        # Create service and delegate all business logic to service
        client_service = ClientService(
            db_connection=db_connection,
        )
        event_service = EventService(db_connection=db_connection)
        await client_service.create_client_from_user(body)
        event = await event_service.create_lifecycle_event(
            event_type=ClientEventType.CREATED.value,
            aggregate_id=body.user_id,
            organization_id=body.organization_id,
            actor_user_id=body.user_id,
            payload={"module": "clients", "action": "create_from_user"},
            topics=CLIENT_KAFKA_TOPICS,
        )

    if event is not None:
        background_tasks.add_task(
            EventService.publish_event_background,
            event=event,
            key=str(body.user_id),
            topics=CLIENT_KAFKA_TOPICS,
        )

    return success_response(
        request=request,
        message_key="clients.success.client_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
    )


@handle_api_exceptions("create client")
@router.post(
    "",
    status_code=http_status.HTTP_201_CREATED,
    description="Create a new client",
    summary="Create a new client",
    responses={
        http_status.HTTP_201_CREATED: {"description": "Client created successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Organization not found"},
        http_status.HTTP_409_CONFLICT: {
            "description": "Email, phone, or company name already exists"
        },
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
    },
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="pii",
    compliance_tags=[
        "gdpr",  # Client creation involves personal information
        "pii",  # Client data contains personally identifiable information
        "soc2_audit",  # Client management is critical for SOC2 compliance
        "audit_required",  # Client creation requires audit trail
    ],
    table_name="clients",
    category="CLIENT",
)
async def create_client(
    request: Request,
    background_tasks: BackgroundTasks,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    sb_client: AsyncClient = Depends(supabase_service),
    body: CreateClientRequest = Body(...),
):
    """Create a new client with complete onboarding flow.

    Orchestrates the full client creation process including authentication setup,
    user provisioning across systems (Supabase and Isometrik), client record creation,
    and optional lead and address records. Sends a welcome email upon successful creation.
    Enrichment runs as a background task after the response; it uses its own DB connection
    so it is independent of the API request. We commit the client-creation transaction
    before scheduling the task and returning, so the enrichment task always sees the row.
    """
    # Set audit context for client creation
    client_name = body.name or f"{body.first_name} {body.last_name}"
    request.state.audit_table = "clients"
    request.state.audit_description = f"Created new client: {client_name}"
    request.state.audit_risk_level = "high"

    # Single transaction for permissions + client creation
    async with db_connection.transaction():
        user_context = await check_permissions(
            current_user=current_user,
            db_connection=db_connection,
            permission_codes=CLIENTS_MANAGEMENT_CREATE,
        )

        client_service = ClientService(
            user_context=user_context,
            db_connection=db_connection,
            supabase_client=sb_client,
        )
        event_service = EventService(db_connection=db_connection)
        result = await client_service.create_client(request_data=body)
        create_events = await event_service.create_client_created_events(
            records=result.records,
            actor_user_id=str(user_context.user_id) if user_context.user_id else None,
            topics=CLIENT_KAFKA_TOPICS,
        )

    # Committed; run enrichment for each created client (person and/or company).
    if result.enrichment_items:
        payload_data = body.model_dump(mode="json")
        enrichment_service = ClientEnrichmentService.from_settings()
        background_tasks.add_task(
            enrichment_service.run_bulk_client_enrichment,
            result.enrichment_items,
            payload_data,
        )

    # Best-effort Typesense indexing, offloaded to background task (uses own DB connection).
    if result.records:
        client_refs = [(str(r["id"]), str(r["organization_id"])) for r in result.records]
        background_tasks.add_task(
            ClientService.index_clients_in_typesense_background,
            client_refs,
        )
        for event in create_events:
            client_id = str(event["aggregate_id"])
            background_tasks.add_task(
                EventService.publish_event_background,
                event=event,
                key=client_id,
                topics=CLIENT_KAFKA_TOPICS,
            )

    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }

    return success_response(
        request=request,
        message_key="clients.success.client_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
    )


@handle_api_exceptions("list clients")
@router.get(
    "",
    status_code=http_status.HTTP_200_OK,
    description="List all clients with filtering and pagination",
    summary="List all clients",
    responses={
        http_status.HTTP_200_OK: {"description": "Clients retrieved successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
    },
)
@limiter.limit("100/minute")
async def list_clients(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    search: str | None = Query(None, min_length=2, description="Search term"),
    client_type: ClientType | None = Query(
        None,
        description="Filter by client type (person, company)",
        enum=[ClientType.PERSON.value, ClientType.COMPANY.value],
    ),
    status: ClientStatus | None = Query(None, description="Filter by status"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Page size"),
):
    """List all clients with optional filtering and pagination."""
    # Check permissions and get user context
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=CLIENTS_MANAGEMENT_VIEW,
    )
    # Store query params in dict
    filter_params = {
        "search": search,
        "client_type": client_type.value if client_type else None,
        "status": status.value if status else None,
        "page": page,
        "page_size": page_size,
    }

    client_service = ClientService(user_context=user_context, db_connection=db_connection)
    result = await client_service.get_clients_list(
        organization_id=user_context.organization_id,
        filter_params=filter_params,
    )

    clients = result["clients"]
    total = result["total"]

    if not clients:
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
        items=clients,
        total=total,
        page=page,
        page_size=page_size,
        message_key="clients.success.clients_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("search clients")
@router.get(
    "/search",
    status_code=http_status.HTTP_200_OK,
    description="Search clients using Typesense (hybrid keyword + vector search)",
    summary="Search clients",
    responses={
        http_status.HTTP_200_OK: {"description": "Clients retrieved successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
    },
)
@limiter.limit("100/minute")
async def search_clients(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    query: str = Query(..., min_length=2, description="Search query string"),
    client_type: ClientType | None = Query(
        None,
        description="Filter by client type (person, company)",
        enum=[ClientType.PERSON.value, ClientType.COMPANY.value],
    ),
    status: ClientStatus | None = Query(None, description="Filter by status"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Page size"),
):
    """Search clients via Typesense with organization-scoped filters."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=CLIENTS_MANAGEMENT_VIEW,
    )

    client_service = ClientService(user_context=user_context, db_connection=db_connection)
    result = await client_service.search_clients(
        organization_id=user_context.organization_id,
        query=query,
        page=page,
        page_size=page_size,
        client_type=client_type.value if client_type else None,
        status=status.value if status else None,
    )

    clients = result["clients"]
    total = result["total"]

    if not clients:
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
        items=clients,
        total=total,
        page=page,
        page_size=page_size,
        message_key="clients.success.clients_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("get client details")
@router.get(
    "/{client_id}",
    status_code=http_status.HTTP_200_OK,
    description="Get client details by ID",
    summary="Get client details",
    response_model=ClientDetailsResponse,
    responses={
        http_status.HTTP_200_OK: {"description": "Client details retrieved successfully"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Client not found"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
    },
)
@limiter.limit("100/minute")
async def get_client_details(
    request: Request,
    client_id: str = Path(..., description="Client ID"),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Get client details by ID with all fields and addresses."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=CLIENTS_MANAGEMENT_VIEW,
    )

    client_service = ClientService(
        user_context=user_context,
        db_connection=db_connection,
    )
    client_details = await client_service.get_client_details(
        client_id, user_context.organization_id
    )

    return success_response(
        request=request,
        message_key="clients.success.client_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=client_details.model_dump(exclude_none=True),
    )


@handle_api_exceptions("delete client")
@router.delete(
    "/{client_id}",
    status_code=http_status.HTTP_200_OK,
    description="Delete a client (soft delete)",
    summary="Delete client",
    responses={
        http_status.HTTP_200_OK: {"description": "Client deleted successfully"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Client not found"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
    },
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="pii",
    compliance_tags=[
        "gdpr",  # Client deletion involves personal information
        "pii",  # Client data contains personally identifiable information
        "soc2_audit",  # Client management is critical for SOC2 compliance
        "audit_required",  # Client deletion requires audit trail
    ],
    table_name="clients",
    category="CLIENT",
)
async def delete_client(
    request: Request,
    background_tasks: BackgroundTasks,
    client_id: str = Path(..., description="Client ID"),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Soft delete a client by setting status='deleted'."""
    request.state.audit_table = "clients"
    request.state.audit_requested_id = client_id
    request.state.audit_description = f"Deleted client: {client_id}"
    request.state.audit_risk_level = "high"

    event: dict | None = None
    async with db_connection.transaction():
        # Check permissions and get user context
        user_context = await check_permissions(
            current_user=current_user,
            db_connection=db_connection,
            permission_codes=CLIENTS_MANAGEMENT_DELETE,
        )
        if not user_context.organization_id:
            raise ValueError("Organization ID is required")

        request.state.audit_user_context = {
            "user_id": user_context.user_id,
            "user_email": user_context.email,
            "organization_id": user_context.organization_id,
        }

        # Create service with user context and delegate to service
        client_service = ClientService(
            user_context=user_context,
            db_connection=db_connection,
        )
        event_service = EventService(db_connection=db_connection)
        await client_service.delete_client(client_id, user_context.organization_id)
        event = await event_service.create_lifecycle_event(
            event_type=ClientEventType.DELETED.value,
            aggregate_id=client_id,
            organization_id=user_context.organization_id,
            actor_user_id=str(user_context.user_id) if user_context.user_id else None,
            payload={"module": "clients", "action": "delete"},
            topics=CLIENT_KAFKA_TOPICS,
        )

    # Transaction committed; it is now safe to publish the lifecycle event.
    background_tasks.add_task(ClientService.delete_clients_from_typesense_background, [client_id])
    if event is not None:
        background_tasks.add_task(
            EventService.publish_event_background,
            event=event,
            key=client_id,
            topics=CLIENT_KAFKA_TOPICS,
        )

    return success_response(
        request=request,
        message_key="clients.success.client_deleted",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("update client")
@router.patch(
    "/{client_id}",
    status_code=http_status.HTTP_200_OK,
    description="Update a client (partial)",
    summary="Update client",
    responses={
        http_status.HTTP_200_OK: {"description": "Client updated successfully"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Client not found"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
    },
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "soc2_audit", "audit_required"],
    table_name="clients",
    category="CLIENT",
)
async def update_client(
    request: Request,
    background_tasks: BackgroundTasks,
    client_id: str = Path(..., description="Client ID"),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    body: UpdateClientRequest = Body(...),
):
    """Update client by ID. Only provided fields are applied."""
    request.state.audit_table = "clients"
    request.state.audit_requested_id = client_id
    request.state.audit_description = f"Updated client: {client_id}"
    request.state.audit_risk_level = "medium"

    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=CLIENTS_MANAGEMENT_EDIT,
    )
    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }

    update_event: dict | None = None
    async with db_connection.transaction():
        client_service = ClientService(
            user_context=user_context,
            db_connection=db_connection,
        )
        event_service = EventService(db_connection=db_connection)
        result = await client_service.update_client(client_id, user_context.organization_id, body)

        if result:
            request.state.raw_audit_old_data = result.get("old_data")
            request.state.raw_audit_new_data = body.model_dump(
                exclude_unset=True, exclude_none=True
            )
            changed_fields = list(body.model_dump(exclude_unset=True, exclude_none=True).keys())
            update_event = await event_service.create_lifecycle_event(
                event_type=ClientEventType.UPDATED.value,
                aggregate_id=client_id,
                organization_id=user_context.organization_id,
                actor_user_id=str(user_context.user_id) if user_context.user_id else None,
                payload={
                    "module": "clients",
                    "action": "update",
                    "changed_fields": changed_fields,
                },
                topics=CLIENT_KAFKA_TOPICS,
            )

    # Best-effort Typesense indexing after update, offloaded to background task.
    background_tasks.add_task(
        ClientService.index_clients_in_typesense_background,
        [(client_id, user_context.organization_id)],
    )

    # Trigger enrichment only when enrichment-relevant inputs have changed.
    enrichment_input_fields = (
        "company_name",
        "industry",
        "websites",
        "social_pages",
        "addresses",
        "primary_contact",
    )
    enrichment_inputs_changed = any(
        getattr(body, field_name) is not None for field_name in enrichment_input_fields
    )
    if enrichment_inputs_changed:
        background_tasks.add_task(
            ClientService.trigger_enrichment_background,
            client_id,
            user_context.organization_id,
        )
    if update_event is not None:
        background_tasks.add_task(
            EventService.publish_event_background,
            event=update_event,
            key=client_id,
            topics=CLIENT_KAFKA_TOPICS,
        )

    return success_response(
        request=request,
        message_key="clients.success.client_updated",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("enrich client")
@router.post(
    "/enrich/{client_id}",
    status_code=http_status.HTTP_202_ACCEPTED,
    description="Trigger enrichment for a client using current data",
    summary="Enrich client",
    responses={
        http_status.HTTP_202_ACCEPTED: {"description": "Client enrichment requested"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Client not found"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
    },
)
@limiter.limit("50/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "soc2_audit", "audit_required"],
    table_name="clients",
    category="CLIENT",
)
async def enrich_client(
    request: Request,
    background_tasks: BackgroundTasks,
    client_id: str = Path(..., description="Client ID"),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Trigger client enrichment by client ID."""
    request.state.audit_table = "clients"
    request.state.audit_requested_id = client_id
    request.state.audit_description = f"Enriched client: {client_id}"
    request.state.audit_risk_level = "medium"

    organization_id = None
    async with db_connection.transaction():
        user_context = await check_permissions(
            current_user=current_user,
            db_connection=db_connection,
            permission_codes=CLIENTS_MANAGEMENT_EDIT,
        )
        request.state.audit_user_context = {
            "user_id": user_context.user_id,
            "user_email": user_context.email,
            "organization_id": user_context.organization_id,
        }
        organization_id = user_context.organization_id
        event_service = EventService(db_connection=db_connection)
        enrich_event = await event_service.create_lifecycle_event(
            event_type=ClientEventType.ENRICHMENT_REQUESTED.value,
            aggregate_id=client_id,
            organization_id=organization_id,
            actor_user_id=str(user_context.user_id) if user_context.user_id else None,
            payload={"module": "clients", "action": "enrich"},
            topics=CLIENT_KAFKA_TOPICS,
        )

    background_tasks.add_task(
        ClientService.trigger_enrichment_background,
        client_id,
        organization_id,
    )
    background_tasks.add_task(
        EventService.publish_event_background,
        event=enrich_event,
        key=client_id,
        topics=CLIENT_KAFKA_TOPICS,
    )

    return success_response(
        request=request,
        message_key="clients.success.client_enrichment_requested",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_202_ACCEPTED,
    )
