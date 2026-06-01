"""External Clients API.

These endpoints are intended for external integrations (partners, embedded apps)
that need to access *clients* data but do not authenticate with our JWT bearer
token. Instead, the caller authenticates via Isometrik credential decode using
headers:

- ``licenseKey``
- ``appSecret``

The decoded ``projectId`` is mapped to our internal ``organization_id`` and all
reads are scoped to that organization.
"""

import json
from typing import Any
from uuid import UUID

import asyncpg
from fastapi import APIRouter, BackgroundTasks, Body, Depends, Path, Query, Request
from fastapi import status as http_status
from supabase import AsyncClient

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn
from apps.user_service.app.dependencies.external_auth import get_organization_context
from apps.user_service.app.dependencies.supabase import supabase_service
from apps.user_service.app.schemas.companies import (
    CompanyDetailsResponse,
    CompanySummaryResponse,
    CreateCompanyRequest,
    UpdateCompanyRequest,
)
from apps.user_service.app.schemas.contacts import (
    ContactBasicInfoResponse,
    ContactDetailsResponse,
    ContactSummaryResponse,
    CreateContactRequest,
    GetContactsByIdsRequest,
    UpdateContactRequest,
)
from apps.user_service.app.schemas.contacts_imports import (
    CreateContactsImportJobResponse,
    ExternalCreateContactsImportJobRequest,
    GetContactsImportJobResponse,
    RetryContactsImportJobResponse,
)
from apps.user_service.app.schemas.enums import (
    ClientStatus,
    CompanyEventType,
    ContactEventType,
    KafkaTopics,
)
from apps.user_service.app.schemas.external_clients import (
    ExternalContactFieldsByPhoneRequest,
    ExternalContactFieldValue,
    ExternalCreateCompanyResult,
    ExternalCreateContactResult,
)
from apps.user_service.app.services.client_enrichment_service import (
    ClientEnrichmentService,
)
from apps.user_service.app.services.companies_service import CompaniesService
from apps.user_service.app.services.contacts_imports_service import (
    CONTACTS_IMPORT_TOPIC,
    ContactsImportService,
)
from apps.user_service.app.services.contacts_service import ContactsService
from apps.user_service.app.services.event_service import EventService
from apps.user_service.app.services.typesense_index_service import (
    delete_company_background,
    delete_contact_background,
    index_companies_background,
    index_contacts_background,
)
from apps.user_service.app.utils.common_utils import (
    UserContext,
    handle_api_exceptions,
)
from libs.shared_utils.http_exceptions import NotFoundException
from libs.shared_utils.logger import get_logger
from libs.shared_utils.response_factory import (
    list_response,
    success_response,
)
from libs.shared_utils.status_codes import CustomStatusCode

# External integrations should not share the same path-space as internal `/clients/*`
# to avoid collisions with `/clients/{client_id}` routes.
router = APIRouter(prefix="/integrations/clients", tags=["Clients (External)"])

CLIENT_KAFKA_TOPICS: list[KafkaTopics] = [KafkaTopics.CRM_EVENTS]

logger = get_logger(__name__)

CONTACTS_IMPORT_ERROR_RESPONSES: dict[int | str, dict] = {
    http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
    http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    http_status.HTTP_404_NOT_FOUND: {"description": "Not found"},
    http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
    http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
    http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
}


def _external_user_context(*, organization_id: str, actor_email: str | None) -> UserContext:
    """External user context."""
    return UserContext(
        user_id="external_integration",
        email=actor_email or "external_integration@system.local",
        organization_id=organization_id,
    )


def _mask_phone_number(value: str | None) -> str | None:
    """Mask phone number."""
    if not value:
        return value
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) <= 4:
        return "***"
    return f"***{digits[-4:]}"


@handle_api_exceptions("external list companies")
@router.get(
    "/companies",
    status_code=http_status.HTTP_200_OK,
    summary="List companies (external auth)",
    description=(
        "List company-type clients for the organization resolved from "
        "Isometrik credentials (`licenseKey`/`appSecret`). Supports search, "
        "status filtering, and pagination."
    ),
    responses={
        http_status.HTTP_200_OK: {"description": "Companies retrieved successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Not found"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
    },
)
@limiter.limit("200/minute")
async def external_list_companies(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    organization_id: str = Depends(get_organization_context),
    search: str | None = Query(None, min_length=2, description="Search term"),
    status: ClientStatus | None = Query(None, description="Filter by status"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Page size"),
):
    """External list companies endpoint (Isometrik credential auth)."""
    service = CompaniesService(
        db_connection=db_connection,
        user_context=_external_user_context(
            organization_id=organization_id,
            actor_email=getattr(request.state, "external_actor_email", None),
        ),
    )
    result = await service.list_companies(
        search=search,
        status=status.value if status else None,
        page=page,
        page_size=page_size,
    )
    items = [
        CompanySummaryResponse.model_validate(summary_row).model_dump(
            exclude_none=True, mode="json"
        )
        for summary_row in (result.get("items") or [])
    ]
    return list_response(
        request=request,
        items=items,
        total=int(result.get("total") or 0),
        page=page,
        page_size=page_size,
        message_key="clients.success.clients_retrieved",
        custom_code=CustomStatusCode.SUCCESS if items else CustomStatusCode.NO_CONTENT,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("external list contacts")
@router.get(
    "/contacts",
    status_code=http_status.HTTP_200_OK,
    summary="List contacts (external auth)",
    description=(
        "List person-type clients (contacts) for the organization resolved from "
        "Isometrik credentials (`licenseKey`/`appSecret`). Supports search, "
        "status filtering, and pagination."
    ),
    responses={
        http_status.HTTP_200_OK: {"description": "Contacts retrieved successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Not found"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
    },
)
@limiter.limit("200/minute")
async def external_list_contacts(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    organization_id: str = Depends(get_organization_context),
    search: str | None = Query(None, min_length=2, description="Search term"),
    status: ClientStatus | None = Query(None, description="Filter by status"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Page size"),
):
    """External list contacts endpoint (Isometrik credential auth)."""
    service = ContactsService(
        db_connection=db_connection,
        user_context=_external_user_context(
            organization_id=organization_id,
            actor_email=getattr(request.state, "external_actor_email", None),
        ),
    )
    result = await service.list_contacts(
        search=search,
        status=status.value if status else None,
        page=page,
        page_size=page_size,
    )
    items = [
        ContactSummaryResponse.model_validate(summary_row).model_dump(
            exclude_none=True, mode="json"
        )
        for summary_row in (result.get("items") or [])
    ]
    return list_response(
        request=request,
        items=items,
        total=int(result.get("total") or 0),
        page=page,
        page_size=page_size,
        message_key="clients.success.clients_retrieved",
        custom_code=CustomStatusCode.SUCCESS if items else CustomStatusCode.NO_CONTENT,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("external lookup contacts by ids")
@router.post(
    "/contacts/lookup",
    status_code=http_status.HTTP_200_OK,
    summary="Lookup contacts by id (external auth)",
    description=(
        "Returns id, display name, and email for each requested contact id in the "
        "organization resolved from Isometrik credentials (`licenseKey`/`appSecret`). "
        "Unknown or deleted ids are omitted."
    ),
    responses={
        http_status.HTTP_200_OK: {"description": "Contacts retrieved successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Not found"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
    },
)
@limiter.limit("200/minute")
async def external_lookup_contacts_by_ids(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    organization_id: str = Depends(get_organization_context),
    body: GetContactsByIdsRequest = Body(...),
):
    """Bulk lookup contacts by id (Isometrik credential auth)."""
    service = ContactsService(
        db_connection=db_connection,
        user_context=_external_user_context(
            organization_id=organization_id,
            actor_email=getattr(request.state, "external_actor_email", None),
        ),
    )
    rows = await service.get_contacts_by_ids(contact_ids=body.contact_ids)
    items = [
        ContactBasicInfoResponse.model_validate(row).model_dump(exclude_none=True, mode="json")
        for row in rows
    ]
    if not items:
        return success_response(
            request=request,
            message_key="success.no_data",
            custom_code=CustomStatusCode.NO_CONTENT,
            status_code=http_status.HTTP_200_OK,
            data=[],
        )
    return success_response(
        request=request,
        message_key="clients.success.clients_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=items,
    )


@handle_api_exceptions("external create company")
@router.post(
    "/companies",
    status_code=http_status.HTTP_201_CREATED,
    summary="Create a company (external auth)",
    description=(
        "Create a company client and its primary contact using Isometrik credential auth. "
        "Payload is company-specific and mapped internally to the existing CreateClientRequest."
    ),
    responses={
        http_status.HTTP_201_CREATED: {"description": "Company created successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_409_CONFLICT: {"description": "Conflict"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
    },
)
@limiter.limit("60/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "soc2_audit", "audit_required"],
    table_name="clients",
    category="CLIENT",
)
async def external_create_company(
    request: Request,
    background_tasks: BackgroundTasks,
    db_connection: asyncpg.Connection = Depends(db_conn),
    sb_client: AsyncClient = Depends(supabase_service),
    organization_id: str = Depends(get_organization_context),
    body: CreateCompanyRequest = Body(...),
):
    """External create company endpoint (Isometrik credential auth)."""
    actor_email = request.state.external_actor_email
    user_context = _external_user_context(organization_id=organization_id, actor_email=actor_email)
    created_events: list[tuple[dict, str]] = []
    result: dict | None = None
    lead_created_event: dict | None = None
    lead_event_key: str | None = None
    async with db_connection.transaction():
        service = CompaniesService(
            db_connection=db_connection,
            user_context=user_context,
            supabase_client=sb_client,
        )
        event_service = EventService(db_connection=db_connection)
        result = await service.create_company(body)
        company_id = str(result["company_id"])
        created_entities = result.get("created_entities") or []
        contact_id = next(
            (
                str(e.get("entity_id"))
                for e in created_entities
                if (
                    e.get("entity_table") == "contacts"
                    and e.get("action") == "create_contact"
                    and e.get("entity_id")
                )
            ),
            None,
        )
        lead_id = result.get("created_lead_id")
        created_events = await CompaniesService.create_lifecycle_events_for_created_entities(
            event_service=event_service,
            created_entities=created_entities,
            organization_id=user_context.organization_id,
            actor_user_id=str(user_context.user_id) if user_context.user_id else None,
        )
        if lead_id:
            lead_created_event = await event_service.create_lead_created_lifecycle_event(
                lead_id=str(lead_id),
                organization_id=user_context.organization_id,
                actor_user_id=str(user_context.user_id) if user_context.user_id else None,
                topics=CLIENT_KAFKA_TOPICS,
            )
            lead_event_key = str(lead_id)

    request.state.audit_table = "clients"
    request.state.audit_requested_id = str(company_id) if company_id else ""
    request.state.audit_description = f"Created external company client: {company_id or 'unknown'}"
    request.state.audit_risk_level = "high"
    request.state.audit_user_context = {
        "user_id": "00000000-0000-0000-0000-000000000000",
        "user_email": actor_email,
        "organization_id": organization_id,
    }
    request.state.raw_audit_new_data = {
        "company_id": str(company_id) if company_id else None,
        "contact_id": str(contact_id) if contact_id else None,
        "lead_id": lead_id,
        "result": result or {},
    }

    CompaniesService.schedule_lifecycle_event_publishes(
        background_tasks=background_tasks,
        created_events=created_events,
    )
    if lead_created_event is not None and lead_event_key is not None:
        background_tasks.add_task(
            EventService.publish_event_background,
            event=lead_created_event,
            key=lead_event_key,
            topics=CLIENT_KAFKA_TOPICS,
        )

    # Typesense indexing (best-effort)
    background_tasks.add_task(
        index_companies_background,
        [(company_id, organization_id)],
    )
    for entity in created_entities:
        if (
            entity.get("entity_table") == "contacts"
            and entity.get("action") == "create_contact"
            and entity.get("entity_id")
        ):
            background_tasks.add_task(
                index_contacts_background,
                [(str(entity["entity_id"]), organization_id)],
            )

    # Enrichment (best-effort) after commit
    enrichment_service = ClientEnrichmentService.from_settings()
    for item in (result or {}).get("enrichment_targets") or []:
        background_tasks.add_task(
            enrichment_service.run_client_enrichment,
            client_id=item["client_id"],
            organization_id=item["organization_id"],
            client_type=item["client_type"],
            payload_data=item.get("payload_data") or {},
            entity_table=item.get("entity_table") or "clients",
            skip_company_logo=bool(item.get("skip_company_logo")),
        )

    return success_response(
        request=request,
        message_key="clients.success.client_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=ExternalCreateCompanyResult(
            company_id=str(company_id),
            contact_id=str(contact_id) if contact_id else None,
            lead_id=lead_id,
        ).model_dump(exclude_none=True, mode="json"),
    )


@handle_api_exceptions("external create contact")
@router.post(
    "/contacts",
    status_code=http_status.HTTP_201_CREATED,
    summary="Create a contact (external auth)",
    description=(
        "Create a contact/person client using Isometrik credential auth. "
        "Payload is contact-specific and mapped internally to the existing CreateClientRequest."
    ),
    responses={
        http_status.HTTP_201_CREATED: {"description": "Contact created successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_409_CONFLICT: {"description": "Conflict"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
    },
)
@limiter.limit("60/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "soc2_audit", "audit_required"],
    table_name="clients",
    category="CLIENT",
)
async def external_create_contact(
    request: Request,
    background_tasks: BackgroundTasks,
    db_connection: asyncpg.Connection = Depends(db_conn),
    sb_client: AsyncClient = Depends(supabase_service),
    organization_id: str = Depends(get_organization_context),
    body: CreateContactRequest = Body(...),
):
    """External create contact endpoint (Isometrik credential auth)."""
    actor_email = request.state.external_actor_email
    user_context = _external_user_context(organization_id=organization_id, actor_email=actor_email)
    created_events: list[tuple[dict, str]] = []
    result: dict | None = None
    lead_created_event: dict | None = None
    lead_event_key: str | None = None
    async with db_connection.transaction():
        service = ContactsService(
            db_connection=db_connection,
            user_context=user_context,
            supabase_client=sb_client,
        )
        event_service = EventService(db_connection=db_connection)
        result = await service.create_contact(body)
        contact_id = str(result["contact_id"])
        company_id = str(result["company_id"]) if result.get("company_id") else None
        lead_id = result.get("created_lead_id")
        created_events = await ContactsService.create_lifecycle_events_for_created_entities(
            event_service=event_service,
            created_entities=result.get("created_entities"),
            organization_id=user_context.organization_id,
            actor_user_id=str(user_context.user_id) if user_context.user_id else None,
        )
        if lead_id:
            lead_created_event = await event_service.create_lead_created_lifecycle_event(
                lead_id=str(lead_id),
                organization_id=user_context.organization_id,
                actor_user_id=str(user_context.user_id) if user_context.user_id else None,
                topics=CLIENT_KAFKA_TOPICS,
            )
            lead_event_key = str(lead_id)

    request.state.audit_table = "clients"
    request.state.audit_requested_id = str(contact_id) if contact_id else ""
    request.state.audit_description = f"Created external contact client: {contact_id or 'unknown'}"
    request.state.audit_risk_level = "high"
    request.state.audit_user_context = {
        "user_id": "00000000-0000-0000-0000-000000000000",
        "user_email": actor_email,
        "organization_id": organization_id,
    }
    request.state.raw_audit_new_data = {
        "company_id": str(company_id) if company_id else None,
        "contact_id": str(contact_id) if contact_id else None,
        "lead_id": lead_id,
        "result": result or {},
    }

    ContactsService.schedule_lifecycle_event_publishes(
        background_tasks=background_tasks,
        created_events=created_events,
    )
    if lead_created_event is not None and lead_event_key is not None:
        background_tasks.add_task(
            EventService.publish_event_background,
            event=lead_created_event,
            key=lead_event_key,
            topics=CLIENT_KAFKA_TOPICS,
        )

    background_tasks.add_task(
        index_contacts_background,
        [(contact_id, organization_id)],
    )
    for entity in (result or {}).get("created_entities") or []:
        entity_identifier = entity.get("entity_id")
        if not entity_identifier:
            continue
        if entity.get("entity_table") == "companies" and entity.get("action") == "create_company":
            background_tasks.add_task(
                index_companies_background,
                [(str(entity_identifier), organization_id)],
            )

    enrichment_service = ClientEnrichmentService.from_settings()
    for item in (result or {}).get("enrichment_targets") or []:
        background_tasks.add_task(
            enrichment_service.run_client_enrichment,
            client_id=item["client_id"],
            organization_id=item["organization_id"],
            client_type=item["client_type"],
            payload_data=item.get("payload_data") or {},
            entity_table=item.get("entity_table") or "clients",
            skip_company_logo=bool(item.get("skip_company_logo")),
        )

    return success_response(
        request=request,
        message_key="clients.success.client_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=ExternalCreateContactResult(
            contact_id=str(contact_id),
            company_id=company_id,
            lead_id=lead_id,
        ).model_dump(exclude_none=True, mode="json"),
    )


@handle_api_exceptions("external get company details")
@router.get(
    "/companies/{client_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Get company details (external auth)",
    description=(
        "Fetch a single company client by ID for the organization resolved from "
        "Isometrik credentials (`licenseKey`/`appSecret`). If the ID exists but is "
        "not a company, this returns 404 to match 'not found' semantics."
    ),
    responses={
        http_status.HTTP_200_OK: {"description": "Company retrieved successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Client not found"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
    },
)
@limiter.limit("200/minute")
async def external_get_company_details(
    request: Request,
    client_id: str = Path(..., description="Client ID"),
    db_connection: asyncpg.Connection = Depends(db_conn),
    organization_id: str = Depends(get_organization_context),
):
    """External get company details endpoint (Isometrik credential auth)."""
    service = CompaniesService(
        db_connection=db_connection,
        user_context=_external_user_context(
            organization_id=organization_id,
            actor_email=getattr(request.state, "external_actor_email", None),
        ),
    )
    details = await service.get_company_details(company_id=client_id)
    return success_response(
        request=request,
        message_key="clients.success.client_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=CompanyDetailsResponse.model_validate(details).model_dump(
            exclude_none=True,
            mode="json",
        ),
    )


@handle_api_exceptions("external get contact details")
@router.get(
    "/contacts/{client_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Get contact details (external auth)",
    description=(
        "Fetch a single person client (contact) by ID for the organization resolved from "
        "Isometrik credentials (`licenseKey`/`appSecret`). If the ID exists but is not a "
        "person/contact, this returns 404 to match 'not found' semantics."
    ),
    responses={
        http_status.HTTP_200_OK: {"description": "Contact retrieved successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Client not found"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
    },
)
@limiter.limit("200/minute")
async def external_get_contact_details(
    request: Request,
    client_id: str = Path(..., description="Client ID"),
    db_connection: asyncpg.Connection = Depends(db_conn),
    organization_id: str = Depends(get_organization_context),
):
    """External get contact details endpoint (Isometrik credential auth)."""
    service = ContactsService(
        db_connection=db_connection,
        user_context=_external_user_context(
            organization_id=organization_id,
            actor_email=getattr(request.state, "external_actor_email", None),
        ),
    )
    details = await service.get_contact_details(contact_id=client_id)
    return success_response(
        request=request,
        message_key="clients.success.client_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=ContactDetailsResponse.model_validate(details).model_dump(
            exclude_none=True,
            mode="json",
        ),
    )


@handle_api_exceptions("external get contact fields by phone")
@router.post(
    "/contacts/by-phone",
    status_code=http_status.HTTP_200_OK,
    response_model=list[ExternalContactFieldValue],
    summary="Get selected contact fields by phone number (external auth)",
    description=(
        "Given a phone number and a list of requested field keys, returns values from the "
        "first matching contact in the organization resolved from Isometrik credentials "
        "(`licenseKey`/`appSecret`)."
    ),
    responses={
        http_status.HTTP_200_OK: {"description": "Contact fields retrieved successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Contact not found"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
    },
)
@limiter.limit("200/minute")
async def external_get_contact_fields_by_phone(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    body: ExternalContactFieldsByPhoneRequest = Body(...),
):
    """External get contact fields by phone endpoint (Isometrik credential auth)."""
    organization_id = UUID("381b7581-8c6b-4e88-b0e7-d9485eecfecc")
    request.state.external_actor_email = None
    user_context = _external_user_context(organization_id=organization_id, actor_email=None)

    raw_body_bytes = await request.body()
    raw_body_text = raw_body_bytes.decode("utf-8", errors="replace")
    logger.info("external_get_contact_fields_by_phone raw_body %s", raw_body_text)

    phone_number = body.phone_number
    variable_keys = body.variable_keys

    req_log = {
        "path": str(request.url.path),
        "organization_id": str(organization_id),
        "phone_number_masked": _mask_phone_number(phone_number),
        "variable_keys": list(variable_keys or []),
    }
    logger.info("external_get_contact_fields_by_phone request %s", json.dumps(req_log))

    service = ContactsService(db_connection=db_connection, user_context=user_context)
    items = await service.get_contact_fields_by_phone(
        phone_number=phone_number,
        variable_keys=variable_keys,
    )

    resp_log = {
        "path": str(request.url.path),
        "organization_id": str(organization_id),
        "items_count": len(items or []),
        "items": [
            (
                it
                if isinstance(it, dict)
                else {
                    "variable_key": getattr(it, "variable_key", None),
                    "variable_value": getattr(it, "variable_value", None),
                }
            )
            for it in (items or [])
        ],
    }
    logger.info("external_get_contact_fields_by_phone response %s", json.dumps(resp_log))

    return items


@handle_api_exceptions("external update company")
@router.patch(
    "/companies/{client_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Update a company (external auth)",
    description=(
        "Update a company-type client (PATCH semantics). The client is scoped to the "
        "organization resolved from Isometrik credentials (`licenseKey`/`appSecret`). "
        "If the ID exists but is not a company, this returns 404."
    ),
    responses={
        http_status.HTTP_200_OK: {"description": "Company updated successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Client not found"},
        http_status.HTTP_409_CONFLICT: {"description": "Conflict"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
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
async def external_update_company(
    request: Request,
    background_tasks: BackgroundTasks,
    client_id: str = Path(..., description="Client ID"),
    db_connection: asyncpg.Connection = Depends(db_conn),
    organization_id: str = Depends(get_organization_context),
    body: UpdateCompanyRequest = Body(...),
):
    """External update company endpoint (Isometrik credential auth)."""
    actor_email = request.state.external_actor_email

    update_result: dict | None = None
    user_context = _external_user_context(organization_id=organization_id, actor_email=actor_email)
    update_event: dict | None = None
    related_lifecycle_events: list[tuple[dict[str, Any], str]] = []
    async with db_connection.transaction():
        service = CompaniesService(db_connection=db_connection, user_context=user_context)
        event_service = EventService(db_connection=db_connection)
        update_result = await service.update_company(company_id=client_id, body=body)
        changed_fields = list(body.model_dump(exclude_unset=True, exclude_none=True).keys())
        contacts_delta = (
            (update_result.get("contacts_delta") or {}) if isinstance(update_result, dict) else {}
        )
        raw_affected = contacts_delta.get("affected_contact_ids") or []
        affected_contact_ids = list(dict.fromkeys(str(cid) for cid in raw_affected))

        company_payload: dict[str, Any] = {
            "module": "companies",
            "action": "update",
            "changed_fields": changed_fields,
        }
        if affected_contact_ids:
            company_payload["affected_contact_ids"] = affected_contact_ids

        update_event = await event_service.create_lifecycle_event(
            event_type=CompanyEventType.UPDATED.value,
            aggregate_id=client_id,
            organization_id=user_context.organization_id,
            actor_user_id=str(user_context.user_id) if user_context.user_id else None,
            payload=company_payload,
            topics=CLIENT_KAFKA_TOPICS,
        )
        created_cid = contacts_delta.get("created_contact_id")
        created_cid_s = str(created_cid) if created_cid else None
        if affected_contact_ids:
            actor = str(user_context.user_id) if user_context.user_id else None
            org_id = user_context.organization_id
            contact_event_items = [
                {
                    "event_type": (
                        ContactEventType.CREATED.value
                        if created_cid_s is not None and cid_s == created_cid_s
                        else ContactEventType.UPDATED.value
                    ),
                    "aggregate_id": cid_s,
                    "organization_id": org_id,
                    "actor_user_id": actor,
                    "payload": {
                        "module": "contacts",
                        "action": (
                            "contact_created_with_company"
                            if created_cid_s is not None and cid_s == created_cid_s
                            else "contact_association_changed"
                        ),
                        "company_id": client_id,
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
    request.state.audit_table = "clients"
    request.state.audit_requested_id = client_id
    request.state.audit_description = f"Updated external company client: {client_id}"
    request.state.audit_risk_level = "medium"
    request.state.audit_user_context = {
        "user_id": "00000000-0000-0000-0000-000000000000",
        "user_email": actor_email,
        "organization_id": organization_id,
    }
    if update_result:
        request.state.raw_audit_old_data = update_result.get("old_data")
        request.state.raw_audit_new_data = update_result.get("new_data")

    CompaniesService.schedule_company_update_background_tasks(
        background_tasks=background_tasks,
        company_id=client_id,
        organization_id=organization_id,
        body=body,
        update_result=update_result if isinstance(update_result, dict) else None,
        update_event=update_event,
        event_key=client_id,
        event_topics=CLIENT_KAFKA_TOPICS,
        related_lifecycle_events=related_lifecycle_events,
    )

    return success_response(
        request=request,
        message_key="clients.success.client_updated",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("external update contact")
@router.patch(
    "/contacts/{client_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Update a contact (external auth)",
    description=(
        "Update a person-type client (contact) (PATCH semantics). The client is scoped "
        "to the organization resolved from Isometrik credentials (`licenseKey`/`appSecret`). "
        "If the ID exists but is not a contact, this returns 404."
    ),
    responses={
        http_status.HTTP_200_OK: {"description": "Contact updated successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Client not found"},
        http_status.HTTP_409_CONFLICT: {"description": "Conflict"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
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
async def external_update_contact(
    request: Request,
    background_tasks: BackgroundTasks,
    client_id: str = Path(..., description="Client ID"),
    db_connection: asyncpg.Connection = Depends(db_conn),
    organization_id: str = Depends(get_organization_context),
    body: UpdateContactRequest = Body(...),
):
    """External update contact endpoint (Isometrik credential auth)."""
    actor_email = request.state.external_actor_email

    update_result: dict | None = None
    user_context = _external_user_context(organization_id=organization_id, actor_email=actor_email)
    update_event: dict | None = None
    related_lifecycle_events: list[tuple[dict[str, Any], str]] = []
    async with db_connection.transaction():
        service = ContactsService(db_connection=db_connection, user_context=user_context)
        event_service = EventService(db_connection=db_connection)
        update_result = await service.update_contact(contact_id=client_id, body=body)
        changed_fields = list(body.model_dump(exclude_unset=True, exclude_none=True).keys())
        companies_delta = (
            (update_result.get("companies_delta") or {}) if isinstance(update_result, dict) else {}
        )
        raw_affected = companies_delta.get("affected_company_ids") or []
        affected_company_ids = list(dict.fromkeys(str(cid) for cid in raw_affected))

        contact_payload: dict[str, Any] = {
            "module": "contacts",
            "action": "update",
            "changed_fields": changed_fields,
        }
        if affected_company_ids:
            contact_payload["affected_company_ids"] = affected_company_ids

        update_event = await event_service.create_lifecycle_event(
            event_type=ContactEventType.UPDATED.value,
            aggregate_id=client_id,
            organization_id=user_context.organization_id,
            actor_user_id=str(user_context.user_id) if user_context.user_id else None,
            payload=contact_payload,
            topics=CLIENT_KAFKA_TOPICS,
        )
        created_cid = companies_delta.get("created_company_id")
        created_cid_s = str(created_cid) if created_cid else None
        if affected_company_ids:
            actor = str(user_context.user_id) if user_context.user_id else None
            org_id = user_context.organization_id
            company_event_items = [
                {
                    "event_type": (
                        CompanyEventType.CREATED.value
                        if created_cid_s is not None and cid_s == created_cid_s
                        else CompanyEventType.UPDATED.value
                    ),
                    "aggregate_id": cid_s,
                    "organization_id": org_id,
                    "actor_user_id": actor,
                    "payload": {
                        "module": "companies",
                        "action": (
                            "company_created_with_contact"
                            if created_cid_s is not None and cid_s == created_cid_s
                            else "company_association_changed"
                        ),
                        "contact_id": client_id,
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
    request.state.audit_table = "clients"
    request.state.audit_requested_id = client_id
    request.state.audit_description = f"Updated external contact client: {client_id}"
    request.state.audit_risk_level = "medium"
    request.state.audit_user_context = {
        "user_id": "00000000-0000-0000-0000-000000000000",
        "user_email": actor_email,
        "organization_id": organization_id,
    }
    if update_result:
        request.state.raw_audit_old_data = update_result.get("old_data")
        request.state.raw_audit_new_data = update_result.get("new_data")

    ContactsService.schedule_contact_update_background_tasks(
        background_tasks=background_tasks,
        contact_id=client_id,
        organization_id=organization_id,
        body=body,
        update_result=update_result if isinstance(update_result, dict) else None,
        update_event=update_event,
        event_key=client_id,
        event_topics=CLIENT_KAFKA_TOPICS,
        related_lifecycle_events=related_lifecycle_events,
    )

    return success_response(
        request=request,
        message_key="clients.success.client_updated",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("external delete company")
@router.delete(
    "/companies/{client_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Delete a company (external auth)",
    description=(
        "Soft-delete a company-type client. The client is scoped to the organization "
        "resolved from Isometrik credentials (`licenseKey`/`appSecret`). If the ID exists "
        "but is not a company, this returns 404."
    ),
    responses={
        http_status.HTTP_200_OK: {"description": "Company deleted successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Client not found"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
    },
)
@limiter.limit("60/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "soc2_audit", "audit_required"],
    table_name="clients",
    category="CLIENT",
)
async def external_delete_company(
    request: Request,
    background_tasks: BackgroundTasks,
    client_id: str = Path(..., description="Client ID"),
    db_connection: asyncpg.Connection = Depends(db_conn),
    organization_id: str = Depends(get_organization_context),
):
    """External delete company endpoint (Isometrik credential auth)."""
    actor_email = request.state.external_actor_email
    user_context = _external_user_context(organization_id=organization_id, actor_email=actor_email)
    service = CompaniesService(db_connection=db_connection, user_context=user_context)
    event: dict | None = None

    async with db_connection.transaction():
        event_service = EventService(db_connection=db_connection)
        deleted = await service.soft_delete_company(company_id=client_id)
        request.state.raw_audit_old_data = deleted.get("old_data")
        request.state.raw_audit_new_data = deleted.get("new_data")
        event = await event_service.create_lifecycle_event(
            event_type=CompanyEventType.DELETED.value,
            aggregate_id=client_id,
            organization_id=user_context.organization_id,
            actor_user_id=str(user_context.user_id) if user_context.user_id else None,
            payload={"module": "companies", "action": "delete"},
            topics=CLIENT_KAFKA_TOPICS,
        )

    request.state.audit_table = "clients"
    request.state.audit_requested_id = client_id
    request.state.audit_description = f"Deleted external company client: {client_id}"
    request.state.audit_risk_level = "high"
    request.state.audit_user_context = {
        "user_id": "00000000-0000-0000-0000-000000000000",
        "user_email": actor_email,
        "organization_id": organization_id,
    }

    if event is not None:
        background_tasks.add_task(
            EventService.publish_event_background,
            event=event,
            key=client_id,
            topics=CLIENT_KAFKA_TOPICS,
        )

    # Best-effort Typesense deletion, offloaded to background task.
    background_tasks.add_task(delete_company_background, client_id)

    return success_response(
        request=request,
        message_key="clients.success.client_deleted",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("external delete contact")
@router.delete(
    "/contacts/{client_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Delete a contact (external auth)",
    description=(
        "Soft-delete a person-type client (contact). The client is scoped to the "
        "organization resolved from Isometrik credentials (`licenseKey`/`appSecret`). "
        "If the ID exists but is not a contact, this returns 404."
    ),
    responses={
        http_status.HTTP_200_OK: {"description": "Contact deleted successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_404_NOT_FOUND: {"description": "Client not found"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
    },
)
@limiter.limit("60/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "soc2_audit", "audit_required"],
    table_name="clients",
    category="CLIENT",
)
async def external_delete_contact(
    request: Request,
    background_tasks: BackgroundTasks,
    client_id: str = Path(..., description="Client ID"),
    db_connection: asyncpg.Connection = Depends(db_conn),
    organization_id: str = Depends(get_organization_context),
):
    """External delete contact endpoint (Isometrik credential auth)."""
    actor_email = request.state.external_actor_email
    user_context = _external_user_context(organization_id=organization_id, actor_email=actor_email)
    service = ContactsService(db_connection=db_connection, user_context=user_context)
    event: dict | None = None

    async with db_connection.transaction():
        event_service = EventService(db_connection=db_connection)
        deleted = await service.soft_delete_contact(contact_id=client_id)
        request.state.raw_audit_old_data = deleted.get("old_data")
        request.state.raw_audit_new_data = deleted.get("new_data")
        event = await event_service.create_lifecycle_event(
            event_type=ContactEventType.DELETED.value,
            aggregate_id=client_id,
            organization_id=user_context.organization_id,
            actor_user_id=str(user_context.user_id) if user_context.user_id else None,
            payload={"module": "contacts", "action": "delete"},
            topics=CLIENT_KAFKA_TOPICS,
        )

    request.state.audit_table = "clients"
    request.state.audit_requested_id = client_id
    request.state.audit_description = f"Deleted external contact client: {client_id}"
    request.state.audit_risk_level = "high"
    request.state.audit_user_context = {
        "user_id": "00000000-0000-0000-0000-000000000000",
        "user_email": actor_email,
        "organization_id": organization_id,
    }

    if event is not None:
        background_tasks.add_task(
            EventService.publish_event_background,
            event=event,
            key=client_id,
            topics=CLIENT_KAFKA_TOPICS,
        )

    # Best-effort Typesense deletion, offloaded to background task.
    background_tasks.add_task(delete_contact_background, client_id)

    return success_response(
        request=request,
        message_key="clients.success.client_deleted",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("external create contacts import job")
@router.post(
    "/contacts/imports",
    status_code=http_status.HTTP_202_ACCEPTED,
    summary="Create a contacts import job (external auth)",
    description=(
        "Creates a contacts import job and enqueues it for async processing. "
        "Organization is derived from Isometrik credentials (`licenseKey`/`appSecret`)."
    ),
    responses=CONTACTS_IMPORT_ERROR_RESPONSES,
)
@limiter.limit("60/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "soc2_audit", "audit_required"],
    table_name="import_jobs",
    category="CLIENT",
)
async def external_create_contacts_import_job(
    request: Request,
    background_tasks: BackgroundTasks,
    db_connection: asyncpg.Connection = Depends(db_conn),
    organization_id: str = Depends(get_organization_context),
    body: ExternalCreateContactsImportJobRequest = Body(...),
):
    """Create a contacts import job using external (Isometrik) authentication."""
    actor_email = request.state.external_actor_email
    async with db_connection.transaction():
        request.state.audit_table = "import_jobs"
        request.state.audit_description = "Created contacts import job (external)"
        request.state.audit_risk_level = "high"
        request.state.audit_user_context = {
            "user_id": "00000000-0000-0000-0000-000000000000",
            "user_email": actor_email,
            "organization_id": organization_id,
        }

        service = ContactsImportService(db_connection=db_connection)
        job, event_payload = await service.create_job_and_enqueue(
            organization_id=organization_id,
            requested_by=actor_email,
            file_url=str(body.file_url),
            file_type=body.file_type.value,
            schema_version=body.schema_version,
            mapping=body.mapping,
            options=body.options.model_dump(mode="json") if body.options else None,
        )
        request.state.audit_requested_id = str(job["job_id"])
        request.state.raw_audit_new_data = job

    background_tasks.add_task(
        EventService.publish_event_background,
        event=event_payload,
        key=str(job["job_id"]),
        topics=[CONTACTS_IMPORT_TOPIC],
    )

    return success_response(
        request=request,
        message_key="contacts_imports.success.job_created",
        custom_code=CustomStatusCode.ACCEPTED,
        status_code=http_status.HTTP_202_ACCEPTED,
        data=CreateContactsImportJobResponse(
            job_id=str(job["job_id"]),
            status=job["status"],
        ).model_dump(exclude_none=True, mode="json"),
    )


@handle_api_exceptions("external get contacts import job")
@router.get(
    "/contacts/imports/{job_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Get contacts import job status (external auth)",
    responses=CONTACTS_IMPORT_ERROR_RESPONSES,
)
@limiter.limit("200/minute")
async def external_get_contacts_import_job(
    request: Request,
    job_id: str = Path(..., min_length=5, max_length=128, description="Import job ID"),
    db_connection: asyncpg.Connection = Depends(db_conn),
    organization_id: str = Depends(get_organization_context),
):
    """Return status and progress for an external contacts import job."""
    service = ContactsImportService(db_connection=db_connection)
    job = await service.get_job(job_id=job_id, organization_id=organization_id)
    if job is None:
        raise NotFoundException(message_key="contacts_imports.errors.job_not_found")

    data = GetContactsImportJobResponse.from_job_row(job).model_dump(exclude_none=True, mode="json")

    return success_response(
        request=request,
        message_key="contacts_imports.success.job_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=data,
    )


@handle_api_exceptions("external get contacts import errors")
@router.get(
    "/contacts/imports/{job_id}/errors",
    status_code=http_status.HTTP_200_OK,
    summary="List row-level errors (external auth)",
    responses=CONTACTS_IMPORT_ERROR_RESPONSES,
)
@limiter.limit("200/minute")
async def external_get_contacts_import_errors(
    request: Request,
    job_id: str = Path(..., min_length=5, max_length=128, description="Import job ID"),
    db_connection: asyncpg.Connection = Depends(db_conn),
    organization_id: str = Depends(get_organization_context),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=200, description="Page size"),
):
    """List row-level import errors for an external job (no redirects)."""
    service = ContactsImportService(db_connection=db_connection)
    job = await service.get_job(job_id=job_id, organization_id=organization_id)
    if job is None:
        raise NotFoundException(message_key="contacts_imports.errors.job_not_found")

    items, total = await service.list_job_error_rows(
        job_id=job_id,
        organization_id=organization_id,
        page=page,
        page_size=page_size,
    )
    if not items:
        raise NotFoundException(message_key="contacts_imports.errors.errors_not_found")

    return list_response(
        request=request,
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        message_key="contacts_imports.success.errors_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("external retry contacts import job")
@router.post(
    "/contacts/imports/{job_id}/retry",
    status_code=http_status.HTTP_202_ACCEPTED,
    summary="Retry contacts import job (external auth)",
    responses=CONTACTS_IMPORT_ERROR_RESPONSES,
)
@limiter.limit("60/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "soc2_audit", "audit_required"],
    table_name="import_jobs",
    category="CLIENT",
)
async def external_retry_contacts_import_job(
    request: Request,
    background_tasks: BackgroundTasks,
    job_id: str = Path(..., min_length=5, max_length=128, description="Import job ID"),
    db_connection: asyncpg.Connection = Depends(db_conn),
    organization_id: str = Depends(get_organization_context),
):
    """Retry a contacts import job created via external authentication."""
    actor_email = request.state.external_actor_email
    async with db_connection.transaction():
        request.state.audit_table = "import_jobs"
        request.state.audit_requested_id = job_id
        request.state.audit_description = f"Retried contacts import job (external): {job_id}"
        request.state.audit_risk_level = "high"
        request.state.audit_user_context = {
            "user_id": "00000000-0000-0000-0000-000000000000",
            "user_email": actor_email,
            "organization_id": organization_id,
        }

        service = ContactsImportService(db_connection=db_connection)
        result = await service.retry_job_and_enqueue(
            job_id=job_id,
            organization_id=organization_id,
            requested_by=actor_email,
        )
        if result is None:
            raise NotFoundException(message_key="contacts_imports.errors.job_not_found")
        job, event_payload = result
        request.state.raw_audit_new_data = job

    background_tasks.add_task(
        EventService.publish_event_background,
        event=event_payload,
        key=str(job["job_id"]),
        topics=[CONTACTS_IMPORT_TOPIC],
    )

    return success_response(
        request=request,
        message_key="contacts_imports.success.job_queued",
        custom_code=CustomStatusCode.ACCEPTED,
        status_code=http_status.HTTP_202_ACCEPTED,
        data=RetryContactsImportJobResponse(
            job_id=str(job["job_id"]),
            status=job["status"],
        ).model_dump(exclude_none=True, mode="json"),
    )
