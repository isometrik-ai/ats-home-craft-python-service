"""Companies v2 API.

Resource-specific endpoints targeting the split tables (`companies`, `contacts`,
`contact_companies`, `company_addresses`) with the operations defined in
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
from apps.user_service.app.schemas.companies_v2 import (
    CompanyDetailsResponse,
    CompanySummaryResponse,
    CreateCompanyRequest,
    UpdateCompanyRequest,
)
from apps.user_service.app.schemas.enums import ClientEventType, ClientStatus, KafkaTopics
from apps.user_service.app.services.companies_service_v2 import CompaniesServiceV2
from apps.user_service.app.services.client_enrichment_service import ClientEnrichmentService
from apps.user_service.app.services.event_service import EventService
from apps.user_service.app.services.typesense_index_service_v2 import (
    delete_company_background,
    index_companies_background,
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

router = APIRouter(prefix="/companies", tags=["Companies v2"])

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
        "Creates a company in the v2 split-table model. Depending on the payload, this can also "
        "link a contact (existing or created inline) and optionally set it as primary.\n\n"
        "Side effects:\n"
        "- Emits lifecycle events (Kafka topic: CRM events)\n"
        "- Schedules Typesense indexing for the company (and for a contact if one was created inline)\n"
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
    """Create a company (v2); indexes an inline-created contact in Typesense when applicable."""
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
        service = CompaniesServiceV2(
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

        for entity in result.get("created_entities") or []:
            entity_id = entity.get("entity_id")
            if not entity_id:
                continue
            evt = await event_service.create_lifecycle_event(
                event_type=ClientEventType.CREATED.value,
                aggregate_id=str(entity_id),
                organization_id=user_context.organization_id,
                actor_user_id=str(user_context.user_id) if user_context.user_id else None,
                payload={"module": "companies_v2", "action": entity.get("action") or "create"},
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
    if company_id is not None:
        background_tasks.add_task(
            index_companies_background,
            [(company_id, user_context.organization_id)],
        )
        for entity in result.get("created_entities") or []:
            if (
                entity.get("entity_table") == "contacts"
                and entity.get("action") == "create_contact"
                and entity.get("entity_id")
            ):
                background_tasks.add_task(
                    index_contacts_background,
                    [(str(entity["entity_id"]), user_context.organization_id)],
                )
    # Enrichment for created company and optionally created primary contact.
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
    """List companies from PostgreSQL with pagination (v2)."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=CLIENTS_MANAGEMENT_VIEW,
    )
    service = CompaniesServiceV2(db_connection=db_connection, user_context=user_context)
    result = await service.list_companies(
        search=search,
        status=status.value if status else None,
        page=page,
        page_size=page_size,
    )
    items = [CompanySummaryResponse.model_validate(r).model_dump(exclude_none=True) for r in result["items"]]
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
    """Search companies via Typesense (companies collection) (v2)."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=CLIENTS_MANAGEMENT_VIEW,
    )
    service = CompaniesServiceV2(db_connection=db_connection, user_context=user_context)
    raw = await service.search_companies(
        query=query,
        page=page,
        page_size=page_size,
        status=status.value if status else None,
    )
    items = raw["hits"]
    total = raw["total"]
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
    """Get company details including primary contact, member contacts and addresses (v2)."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=CLIENTS_MANAGEMENT_VIEW,
    )
    service = CompaniesServiceV2(db_connection=db_connection, user_context=user_context)
    details = await service.get_company_details(company_id=company_id)
    details = CompanyDetailsResponse.model_validate(details).model_dump(exclude_none=True)
    return success_response(
        request=request,
        message_key="clients.success.client_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=details,
    )


@handle_api_exceptions("update company")
@router.patch(
    "/{company_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Update a company",
    description=(
        "Updates company fields and related nested data (e.g., addresses). "
        "Side effects:\n"
        "- Emits an UPDATED lifecycle event\n"
        "- Schedules Typesense re-indexing for the company"
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
    body: UpdateCompanyRequest = Body(...),
):
    """Patch company fields (v2)."""
    update_event: dict | None = None
    async with db_connection.transaction():
        user_context = await check_permissions(
            current_user=current_user,
            db_connection=db_connection,
            permission_codes=CLIENTS_MANAGEMENT_EDIT,
        )
        service = CompaniesServiceV2(db_connection=db_connection, user_context=user_context)
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
            payload={"module": "companies_v2", "action": "update", "changed_fields": changed_fields},
            topics=CLIENT_KAFKA_TOPICS,
        )

    CompaniesServiceV2.schedule_company_update_background_tasks(
        background_tasks=background_tasks,
        company_id=company_id,
        organization_id=user_context.organization_id,
        body=body,
        update_event=update_event,
        event_key=company_id,
        event_topics=CLIENT_KAFKA_TOPICS,
    )
    return success_response(
        request=request,
        message_key="clients.success.client_updated",
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
    """Soft-delete a company (v2)."""
    event: dict | None = None
    async with db_connection.transaction():
        user_context = await check_permissions(
            current_user=current_user,
            db_connection=db_connection,
            permission_codes=CLIENTS_MANAGEMENT_DELETE,
        )
        service = CompaniesServiceV2(db_connection=db_connection, user_context=user_context)
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
            payload={"module": "companies_v2", "action": "delete"},
            topics=CLIENT_KAFKA_TOPICS,
        )

    if event is not None:
        background_tasks.add_task(
            EventService.publish_event_background,
            event=event,
            key=company_id,
            topics=CLIENT_KAFKA_TOPICS,
        )
    background_tasks.add_task(
        delete_company_background,
        company_id
    )
    return success_response(
        request=request,
        message_key="clients.success.client_deleted",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )

