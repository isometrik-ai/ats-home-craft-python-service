"""Companies API.

Resource-specific endpoints targeting the split tables (`companies`, `contacts`,
`contact_companies`, `company_addresses`) with the operations defined in
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
from apps.user_service.app.schemas.companies import (
    CompanyDetailsResponse,
    CompanySummaryResponse,
    CreateCompanyRequest,
    UpdateCompanyRequest,
)
from apps.user_service.app.schemas.enums import (
    ClientEventType,
    ClientStatus,
    KafkaTopics,
)
from apps.user_service.app.services.activity_service import ActivityService
from apps.user_service.app.services.client_enrichment_service import (
    ClientEnrichmentService,
)
from apps.user_service.app.services.companies_service import CompaniesService
from apps.user_service.app.services.event_service import EventService
from apps.user_service.app.services.typesense_index_service import (
    delete_company_background,
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

router = APIRouter(prefix="/companies", tags=["Companies"])

CLIENT_KAFKA_TOPICS: list[KafkaTopics] = [KafkaTopics.CRM_EVENTS]

COMMON_ERROR_RESPONSES: dict[int | str, dict] = {
    401: {"description": "Unauthorized (missing/invalid JWT)."},
    403: {"description": "Forbidden (insufficient permissions)."},
    404: {"description": "Not found."},
    422: {"description": "Validation error."},
    429: {"description": "Too many requests (rate limited)."},
    500: {"description": "Internal server error."},
}


@handle_api_exceptions("create company")
@router.post(
    "",
    status_code=http_status.HTTP_201_CREATED,
    summary="Create a company",
    description=(
        "Creates a company in the split-table model. Depending on the payload, this can also "
        "link a contact (existing or created inline) and optionally set it as primary."
        "Side effects:"
        "- Emits lifecycle events (Kafka topic: CRM events)"
        "- Schedules Typesense indexing for the company"
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
    table_name="companies",
    category="CLIENT",
)
async def create_company(
    request: Request,
    background_tasks: BackgroundTasks,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    sb_client: AsyncClient = Depends(supabase_service),
    body: CreateCompanyRequest = Body(...),
):
    """Create a company.

    May link or create a contact, emit lifecycle events, index Typesense, and queue enrichment.

    Args:
        request: FastAPI request (audit context).
        background_tasks: Schedules events, indexing, and enrichment.
        db_connection: PostgreSQL connection (request-scoped).
        current_user: Authenticated user claims from JWT.
        sb_client: Supabase client for auth-related operations when needed.
        body: Company create payload.

    Returns:
        Created response envelope (201).
    """
    created_events: list[tuple[dict, str]] = []
    company_id: str | None = None
    async with db_connection.transaction():
        user_context = await check_permissions(
            current_user=current_user,
            db_connection=db_connection,
            permission_codes=CLIENTS_MANAGEMENT_CREATE,
        )
        request.state.audit_table = "companies"
        request.state.audit_description = "Created company"
        request.state.audit_risk_level = "high"
        request.state.audit_user_context = {
            "user_id": user_context.user_id,
            "user_email": user_context.email,
            "organization_id": user_context.organization_id,
        }
        service = CompaniesService(
            db_connection=db_connection,
            user_context=user_context,
            supabase_client=sb_client,
        )
        event_service = EventService(db_connection=db_connection)
        result = await service.create_company(body)
        company_id = result["company_id"]
        request.state.audit_requested_id = str(company_id)
        request.state.audit_description = f"Created company: {company_id}"
        request.state.raw_audit_old_data = result.get("old_data")
        request.state.raw_audit_new_data = result.get("new_data")
        created_events = await CompaniesService.create_lifecycle_events_for_created_entities(
            event_service=event_service,
            created_entities=result.get("created_entities"),
            organization_id=user_context.organization_id,
            actor_user_id=str(user_context.user_id) if user_context.user_id else None,
        )

    CompaniesService.schedule_lifecycle_event_publishes(
        background_tasks=background_tasks,
        created_events=created_events,
    )
    CompaniesService.schedule_typesense_indexing_for_created_entities(
        background_tasks=background_tasks,
        company_id=company_id,
        created_entities=result.get("created_entities"),
        organization_id=user_context.organization_id,
    )
    CompaniesService.schedule_enrichment(
        background_tasks=background_tasks,
        enrichment_targets=result.get("enrichment_targets"),
    )

    return success_response(
        request=request,
        message_key="companies.success.company_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
    )


@handle_api_exceptions("list companies")
@router.get(
    "",
    status_code=http_status.HTTP_200_OK,
    summary="List companies (database)",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def list_companies(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    search: str | None = Query(None, min_length=2),
    status: ClientStatus | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """List companies from PostgreSQL with pagination.

    Args:
        request: FastAPI request.
        db_connection: PostgreSQL connection (request-scoped).
        current_user: Authenticated user claims from JWT.
        search: Optional name search (min 2 characters).
        status: Optional status filter.
        page: 1-based page index.
        page_size: Page size (max 100).

    Returns:
        Paginated list response with company summaries.
    """
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=CLIENTS_MANAGEMENT_VIEW,
    )
    service = CompaniesService(db_connection=db_connection, user_context=user_context)
    result = await service.list_companies(
        search=search,
        status=status.value if status else None,
        page=page,
        page_size=page_size,
    )
    items = [
        CompanySummaryResponse.model_validate(summary_row).model_dump(
            exclude_none=True,
            mode="json",
        )
        for summary_row in result["items"]
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
        message_key="companies.success.companies_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("get company activity")
@router.get(
    "/activity/{company_id}/",
    status_code=http_status.HTTP_200_OK,
    description=(
        "Activity feed for a company. `page` / `page_size` paginate (newest first). "
        "`data` contains flattened lines (often one per changed field). `total` and `total_pages` "
        "refer to audit rows; `len(data)` may be larger than `page_size`."
    ),
    summary="Get company activity",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def get_company_activity(
    request: Request,
    company_id: str = Path(..., description="Company identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Audit log rows per page"),
):
    """Get activity for a company (offset pagination)."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=CLIENTS_MANAGEMENT_VIEW,
    )

    # Ensure company exists (and org-scoped) before returning activity.
    service = CompaniesService(db_connection=db_connection, user_context=user_context)
    await service.get_company_details(company_id=company_id)

    activity_service = ActivityService(user_context=user_context, db_connection=db_connection)
    items, total = await activity_service.get_company_activity(
        company_id=company_id,
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


@handle_api_exceptions("search companies")
@router.get(
    "/search",
    status_code=http_status.HTTP_200_OK,
    summary="Search companies (Typesense)",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def search_companies(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    query: str = Query(..., min_length=2),
    status: ClientStatus | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """Search companies via Typesense (companies collection).

    Args:
        request: FastAPI request.
        db_connection: PostgreSQL connection (request-scoped).
        current_user: Authenticated user claims from JWT.
        query: Search text (min 2 characters).
        status: Optional status filter.
        page: 1-based page index.
        page_size: Page size (max 100).

    Returns:
        Paginated list response with company summaries from Typesense.
    """
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=CLIENTS_MANAGEMENT_VIEW,
    )
    service = CompaniesService(db_connection=db_connection, user_context=user_context)
    result = await service.search_companies(
        query=query,
        page=page,
        page_size=page_size,
        status=status.value if status else None,
    )
    items = [
        CompanySummaryResponse.model_validate(summary_row).model_dump(
            exclude_none=True,
            mode="json",
        )
        for summary_row in result["items"]
    ]
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
        message_key="companies.success.companies_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("get company details")
@router.get(
    "/{company_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Get company details",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def get_company_details(
    request: Request,
    company_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Get company details including linked contacts and addresses.

    Args:
        request: FastAPI request.
        company_id: Company identifier.
        db_connection: PostgreSQL connection (request-scoped).
        current_user: Authenticated user claims from JWT.

    Returns:
        Success response with company detail payload.
    """
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=CLIENTS_MANAGEMENT_VIEW,
    )
    service = CompaniesService(db_connection=db_connection, user_context=user_context)
    details = await service.get_company_details(company_id=company_id)
    details = CompanyDetailsResponse.model_validate(details).model_dump(exclude_none=True)
    return success_response(
        request=request,
        message_key="companies.success.company_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=details,
    )


@handle_api_exceptions("enrich company")
@router.post(
    "/{company_id}/enrich",
    status_code=http_status.HTTP_202_ACCEPTED,
    summary="Trigger company enrichment",
    description="Triggers enrichment for a company using the latest persisted data.",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("60/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "soc2_audit", "audit_required"],
    table_name="companies",
    category="CLIENT",
)
async def enrich_company(
    request: Request,
    background_tasks: BackgroundTasks,
    company_id: str = Path(..., description="Company identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Trigger enrichment for a company (best-effort async)."""
    request.state.audit_table = "companies"
    request.state.audit_requested_id = company_id
    request.state.audit_description = f"Triggered enrichment for company: {company_id}"
    request.state.audit_risk_level = "medium"

    enrich_event: dict | None = None
    organization_id: str | None = None
    payload_data: dict[str, Any] = {}
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

        service = CompaniesService(db_connection=db_connection, user_context=user_context)
        details = await service.get_company_details(company_id=company_id)

        addresses_payload: list[dict[str, Any]] = []
        raw_addresses = details.get("addresses") or []
        if isinstance(raw_addresses, list):
            for addr in raw_addresses:
                if isinstance(addr, dict) and (addr.get("country") or "").strip():
                    addresses_payload.append({"country": (addr.get("country") or "").strip()})

        payload_data = {
            "name": details.get("name"),
            "industry": details.get("industry"),
            "email": details.get("email"),
            "websites": details.get("websites") or [],
            "social_pages": details.get("social_pages") or [],
            "addresses": addresses_payload,
        }

        event_service = EventService(db_connection=db_connection)
        enrich_event = await event_service.create_lifecycle_event(
            event_type=ClientEventType.ENRICHMENT_REQUESTED.value,
            aggregate_id=company_id,
            organization_id=organization_id,
            actor_user_id=str(user_context.user_id) if user_context.user_id else None,
            payload={"module": "companies", "action": "enrich"},
            topics=CLIENT_KAFKA_TOPICS,
        )

    if organization_id is not None:
        enrichment_service = ClientEnrichmentService.from_settings()
        background_tasks.add_task(
            enrichment_service.run_client_enrichment,
            client_id=str(company_id),
            organization_id=str(organization_id),
            client_type="company",
            payload_data=payload_data,
            entity_table="companies",
        )
    if enrich_event is not None:
        background_tasks.add_task(
            EventService.publish_event_background,
            event=enrich_event,
            key=company_id,
            topics=CLIENT_KAFKA_TOPICS,
        )

    return success_response(
        request=request,
        message_key="companies.success.company_enrichment_requested",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_202_ACCEPTED,
    )


@handle_api_exceptions("update company")
@router.patch(
    "/{company_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Update a company",
    description=(
        "Updates company fields and related nested data (e.g., addresses). "
        "May also apply contact association changes when `contact_association` is provided "
        "(same batch shape as `company_association` on PATCH /contacts). "
        "Side effects:\n"
        "- Emits lifecycle events for the company and each contact touched by "
        "`contact_association`\n"
        "- Schedules Typesense re-indexing for the company and those contacts"
    ),
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "soc2_audit", "audit_required"],
    table_name="companies",
    category="CLIENT",
)
async def update_company(
    request: Request,
    background_tasks: BackgroundTasks,
    company_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    sb_client: AsyncClient = Depends(supabase_service),
    body: UpdateCompanyRequest = Body(...),
):
    """Patch company fields, nested data, and optional contact associations.

    Args:
        request: FastAPI request (audit context).
        background_tasks: Schedules events and Typesense re-indexing.
        company_id: Company identifier.
        db_connection: PostgreSQL connection (request-scoped).
        current_user: Authenticated user claims from JWT.
        sb_client: Supabase client.
        body: Partial update payload.

    Returns:
        Success response envelope.
    """
    update_event: dict | None = None
    related_lifecycle_events: list[tuple[dict[str, Any], str]] = []
    async with db_connection.transaction():
        user_context = await check_permissions(
            current_user=current_user,
            db_connection=db_connection,
            permission_codes=CLIENTS_MANAGEMENT_EDIT,
        )
        service = CompaniesService(
            db_connection=db_connection,
            user_context=user_context,
            supabase_client=sb_client,
        )
        event_service = EventService(db_connection=db_connection)
        request.state.audit_table = "companies"
        request.state.audit_requested_id = company_id
        request.state.audit_description = f"Updated company: {company_id}"
        request.state.audit_risk_level = "medium"
        request.state.audit_user_context = {
            "user_id": user_context.user_id,
            "user_email": user_context.email,
            "organization_id": user_context.organization_id,
        }
        result = await service.update_company(company_id=company_id, body=body)
        changed_fields = list(body.model_dump(exclude_unset=True, exclude_none=True).keys())
        request.state.raw_audit_old_data = result.get("old_data")
        request.state.raw_audit_new_data = result.get("new_data")
        update_event = await event_service.create_lifecycle_event(
            event_type=ClientEventType.UPDATED.value,
            aggregate_id=company_id,
            organization_id=user_context.organization_id,
            actor_user_id=str(user_context.user_id) if user_context.user_id else None,
            payload={
                "module": "companies",
                "action": "update",
                "changed_fields": changed_fields,
            },
            topics=CLIENT_KAFKA_TOPICS,
        )

        contacts_delta = (result.get("contacts_delta") or {}) if isinstance(result, dict) else {}
        raw_affected = contacts_delta.get("affected_contact_ids") or []
        affected_contact_ids = list(dict.fromkeys(str(cid) for cid in raw_affected))
        created_cid = contacts_delta.get("created_contact_id")
        created_cid_s = str(created_cid) if created_cid else None
        if affected_contact_ids:
            actor = str(user_context.user_id) if user_context.user_id else None
            org_id = user_context.organization_id
            contact_event_items = [
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
                        "module": "companies",
                        "action": (
                            "contact_created_with_company"
                            if created_cid_s is not None and cid_s == created_cid_s
                            else "contact_association_changed"
                        ),
                        "company_id": company_id,
                    },
                }
                for cid_s in affected_contact_ids
            ]
            contact_events = await event_service.create_lifecycle_events(
                items=contact_event_items,
                topics=CLIENT_KAFKA_TOPICS,
            )
            related_lifecycle_events.extend(
                (event_payload, event_payload["aggregate_id"]) for event_payload in contact_events
            )

    CompaniesService.schedule_company_update_background_tasks(
        background_tasks=background_tasks,
        company_id=company_id,
        organization_id=user_context.organization_id,
        body=body,
        update_result=result if isinstance(result, dict) else None,
        update_event=update_event,
        event_key=company_id,
        event_topics=CLIENT_KAFKA_TOPICS,
        related_lifecycle_events=related_lifecycle_events,
    )
    return success_response(
        request=request,
        message_key="companies.success.company_updated",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("delete company")
@router.delete(
    "/{company_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Delete a company (soft delete)",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "soc2_audit", "audit_required"],
    table_name="companies",
    category="CLIENT",
)
async def delete_company(
    request: Request,
    background_tasks: BackgroundTasks,
    company_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Soft-delete a company.

    Args:
        request: FastAPI request (audit context).
        background_tasks: Schedules Kafka publish and Typesense de-index.
        company_id: Company identifier.
        db_connection: PostgreSQL connection (request-scoped).
        current_user: Authenticated user claims from JWT.

    Returns:
        Success response envelope.
    """
    event: dict | None = None
    async with db_connection.transaction():
        user_context = await check_permissions(
            current_user=current_user,
            db_connection=db_connection,
            permission_codes=CLIENTS_MANAGEMENT_DELETE,
        )
        service = CompaniesService(db_connection=db_connection, user_context=user_context)
        event_service = EventService(db_connection=db_connection)
        request.state.audit_table = "companies"
        request.state.audit_requested_id = company_id
        request.state.audit_description = f"Deleted company: {company_id}"
        request.state.audit_risk_level = "high"
        request.state.audit_user_context = {
            "user_id": user_context.user_id,
            "user_email": user_context.email,
            "organization_id": user_context.organization_id,
        }
        deleted = await service.soft_delete_company(company_id=company_id)
        request.state.raw_audit_old_data = deleted.get("old_data")
        request.state.raw_audit_new_data = deleted.get("new_data")
        event = await event_service.create_lifecycle_event(
            event_type=ClientEventType.DELETED.value,
            aggregate_id=company_id,
            organization_id=user_context.organization_id,
            actor_user_id=str(user_context.user_id) if user_context.user_id else None,
            payload={"module": "companies", "action": "delete"},
            topics=CLIENT_KAFKA_TOPICS,
        )

    if event is not None:
        background_tasks.add_task(
            EventService.publish_event_background,
            event=event,
            key=company_id,
            topics=CLIENT_KAFKA_TOPICS,
        )
    background_tasks.add_task(delete_company_background, company_id)
    return success_response(
        request=request,
        message_key="companies.success.company_deleted",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )
