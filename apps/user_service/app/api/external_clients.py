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

import asyncpg
from fastapi import APIRouter, BackgroundTasks, Body, Depends, Path, Query, Request
from fastapi import status as http_status
from supabase import AsyncClient

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.dependencies.external_auth import external_organization_id
from apps.user_service.app.schemas.external_clients import (
    ExternalCreateCompanyRequest,
    ExternalCreateContactRequest,
    ExternalCreateCompanyResult,
    ExternalCreateContactResult,
    ExternalCompanyDetailsResponse,
    ExternalCompanyListItem,
    ExternalContactDetailsResponse,
    ExternalContactListItem,
    ExternalUpdateCompanyRequest,
    ExternalUpdateContactRequest,
)
from apps.user_service.app.schemas.enums import ClientStatus, ClientType
from apps.user_service.app.services.client_service import ClientEnrichmentService, ClientService
from apps.user_service.app.utils.common_utils import UserContext, handle_api_exceptions
from apps.user_service.app.dependencies.supabase import supabase_service
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

# External integrations should not share the same path-space as internal `/clients/*`
# to avoid collisions with `/clients/{client_id}` routes.
router = APIRouter(prefix="/integrations/clients", tags=["Clients (External)"])


def _resolve_created_ids(records: list[dict]) -> tuple[str | None, str | None]:
    """Return (company_id, contact_id) from created client records."""
    company_id: str | None = None
    contact_id: str | None = None
    for r in records or []:
        if r.get("client_type") == ClientType.COMPANY.value:
            company_id = str(r.get("id")) if r.get("id") else company_id
        elif r.get("client_type") == ClientType.PERSON.value:
            contact_id = str(r.get("id")) if r.get("id") else contact_id
    # In rare cases, a create may only create one row.
    return company_id, contact_id


def _build_filter_params(
    *,
    search: str | None,
    status: ClientStatus | None,
    page: int,
    page_size: int,
    client_type: ClientType,
) -> dict:
    """Build filter parameters for client list queries."""
    return {
        "search": search,
        "client_type": client_type.value,
        "status": status.value if status else None,
        "page": page,
        "page_size": page_size,
    }


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
    organization_id: str = Depends(external_organization_id),
    search: str | None = Query(None, min_length=2, description="Search term"),
    status: ClientStatus | None = Query(None, description="Filter by status"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Page size"),
):
    """External list companies endpoint (Isometrik credential auth)."""
    service = ClientService(db_connection=db_connection)
    filter_params = _build_filter_params(
        search=search,
        status=status,
        page=page,
        page_size=page_size,
        client_type=ClientType.COMPANY,
    )
    result = await service.get_clients_list(
        organization_id=organization_id,
        filter_params=filter_params,
    )

    clients = result["clients"]
    total = result["total"]
    items = [
        ExternalCompanyListItem(
            id=str(item.get("id")),
            company_name=item.get("name") or "",
            status=item.get("status"),
            industry=item.get("industry"),
            tags=item.get("tags") or [],
            image_url=item.get("image_url"),
            created_at=item.get("created_at"),
            updated_at=item.get("updated_at"),
            primary_contact=(  # best-effort
                {
                    "first_name": (item.get("primary_contact") or {}).get("first_name"),
                    "last_name": (item.get("primary_contact") or {}).get("last_name"),
                    "title": (item.get("primary_contact") or {}).get("title"),
                    "email": (item.get("primary_contact") or {}).get("email"),
                    "phones": (item.get("primary_contact") or {}).get("phones") or [],
                }
                if item.get("primary_contact") is not None
                else None
            ),
        ).model_dump(exclude_none=True, mode="json")
        for item in (clients or [])
    ]
    return list_response(
        request=request,
        items=items,
        total=total or 0,
        page=page,
        page_size=page_size,
        message_key="clients.success.clients_retrieved",
        custom_code=CustomStatusCode.SUCCESS if clients else CustomStatusCode.NO_CONTENT,
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
    organization_id: str = Depends(external_organization_id),
    search: str | None = Query(None, min_length=2, description="Search term"),
    status: ClientStatus | None = Query(None, description="Filter by status"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Page size"),
):
    """External list contacts endpoint (Isometrik credential auth)."""
    service = ClientService(db_connection=db_connection)
    filter_params = _build_filter_params(
        search=search,
        status=status,
        page=page,
        page_size=page_size,
        client_type=ClientType.PERSON,
    )
    result = await service.get_clients_list(
        organization_id=organization_id,
        filter_params=filter_params,
    )
    clients = result["clients"]
    total = result["total"]
    items = []
    for item in (clients or []):
        primary = item.get("primary_contact") or {}
        phones = primary.get("phones") or []
        items.append(
            ExternalContactListItem(
                id=str(item.get("id")),
                full_name=item.get("name") or "",
                status=item.get("status"),
                company_id=item.get("company_id"),
                company_name=item.get("company_name"),
                email=primary.get("email"),
                phones=phones,
                title=primary.get("title"),
                tags=item.get("tags") or [],
                image_url=item.get("image_url"),
                created_at=item.get("created_at"),
                updated_at=item.get("updated_at"),
                is_primary_contact=bool(primary.get("is_primary_contact"))
                if "is_primary_contact" in primary
                else None,
            ).model_dump(exclude_none=True, mode="json")
        )
    return list_response(
        request=request,
        items=items,
        total=total or 0,
        page=page,
        page_size=page_size,
        message_key="clients.success.clients_retrieved",
        custom_code=CustomStatusCode.SUCCESS if clients else CustomStatusCode.NO_CONTENT,
        status_code=http_status.HTTP_200_OK,
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
async def external_create_company(
    request: Request,
    background_tasks: BackgroundTasks,
    db_connection: asyncpg.Connection = Depends(db_conn),
    sb_client: AsyncClient = Depends(supabase_service),
    organization_id: str = Depends(external_organization_id),
    body: ExternalCreateCompanyRequest = Body(...),
):
    service_body = body.to_create_client_request()
    user_context = UserContext(
        user_id="external_integration",
        email="external_integration@system.local",
        organization_id=organization_id,
    )
    async with db_connection.transaction():
        service = ClientService(
            user_context=user_context,
            db_connection=db_connection,
            supabase_client=sb_client,
        )
        result = await service.create_client(request_data=service_body)
        company_id, contact_id = _resolve_created_ids(result.records or [])
        lead_id = result.lead_id

    # Enrichment (best-effort) after commit
    if result.enrichment_items:
        enrichment_service = ClientEnrichmentService.from_settings()
        background_tasks.add_task(
            enrichment_service.run_bulk_client_enrichment,
            result.enrichment_items,
            service_body.model_dump(mode="json"),
        )

    # Typesense indexing (best-effort)
    if result.records:
        client_refs = [(str(r["id"]), str(r["organization_id"])) for r in result.records]
        background_tasks.add_task(
            ClientService.index_clients_in_typesense_background,
            client_refs,
        )

    return success_response(
        request=request,
        message_key="clients.success.client_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=ExternalCreateCompanyResult(
            company_id=str(company_id),
            contact_id=str(contact_id),
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
async def external_create_contact(
    request: Request,
    background_tasks: BackgroundTasks,
    db_connection: asyncpg.Connection = Depends(db_conn),
    sb_client: AsyncClient = Depends(supabase_service),
    organization_id: str = Depends(external_organization_id),
    body: ExternalCreateContactRequest = Body(...),
):
    service_body = body.to_create_client_request()
    user_context = UserContext(
        user_id="external_integration",
        email="external_integration@system.local",
        organization_id=organization_id,
    )
    async with db_connection.transaction():
        service = ClientService(
            user_context=user_context,
            db_connection=db_connection,
            supabase_client=sb_client,
        )
        result = await service.create_client(request_data=service_body)
        company_id, contact_id = _resolve_created_ids(result.records or [])
        lead_id = result.lead_id

    if result.enrichment_items:
        enrichment_service = ClientEnrichmentService.from_settings()
        background_tasks.add_task(
            enrichment_service.run_bulk_client_enrichment,
            result.enrichment_items,
            service_body.model_dump(mode="json"),
        )

    if result.records:
        client_refs = [(str(r["id"]), str(r["organization_id"])) for r in result.records]
        background_tasks.add_task(
            ClientService.index_clients_in_typesense_background,
            client_refs,
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
    db_connection: asyncpg.Connection = Depends(db_uow),
    organization_id: str = Depends(external_organization_id),
):
    """External get company details endpoint (Isometrik credential auth)."""
    service = ClientService(db_connection=db_connection)
    details = await service.get_client_details(client_id, organization_id)
    if details.client_type != ClientType.COMPANY:
        # Keep same semantics as not found for wrong type.
        return success_response(
            request=request,
            message_key="clients.errors.not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
            status_code=http_status.HTTP_404_NOT_FOUND,
        )
    return success_response(
        request=request,
        message_key="clients.success.client_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=ExternalCompanyDetailsResponse(
            id=str(details.id),
            organization_id=str(details.organization_id),
            client_type=details.client_type,
            company_name=details.name,
            status=details.status,
            portal_access=details.portal_access,
            industry=details.industry,
            image_url=details.image_url,
            tags=details.tags or [],
            primary_contact=details.primary_contact,
            company_contacts=details.company_contacts or [],
            websites=details.websites or [],
            billing_preferences=details.billing_preferences,
            custom_fields=details.custom_fields or [],
            addresses=details.addresses or [],
            lead=details.lead,
            additional_data=details.additional_data or {},
            sales_intelligence=details.sales_intelligence or {},
            social_pages=details.social_pages or [],
            target_market_segments=details.target_market_segments or [],
            current_tech_stack=details.current_tech_stack or [],
            description=details.description,
            preferred_communication_channels=details.preferred_communication_channels or [],
            industry_specific_terminologies=details.industry_specific_terminologies or [],
            linked_pages=details.linked_pages or [],
            products=details.products or [],
            key_people=details.key_people or [],
            enrichment_done=bool(details.enrichment_done),
            last_enriched_at=details.last_enriched_at,
            created_at=details.created_at,
            updated_at=details.updated_at,
        ).model_dump(exclude_none=True, mode="json"),
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
    db_connection: asyncpg.Connection = Depends(db_uow),
    organization_id: str = Depends(external_organization_id),
):
    """External get contact details endpoint (Isometrik credential auth)."""
    service = ClientService(db_connection=db_connection)
    details = await service.get_client_details(client_id, organization_id)
    if details.client_type != ClientType.PERSON:
        return success_response(
            request=request,
            message_key="clients.errors.not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
            status_code=http_status.HTTP_404_NOT_FOUND,
        )
    return success_response(
        request=request,
        message_key="clients.success.client_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=ExternalContactDetailsResponse(
            id=str(details.id),
            organization_id=str(details.organization_id),
            client_type=details.client_type,
            full_name=details.name,
            status=details.status,
            portal_access=details.portal_access,
            company_id=details.company_id,
            company_name=details.company_name,
            image_url=details.image_url,
            tags=details.tags or [],
            primary_contact=details.primary_contact,
            websites=details.websites or [],
            billing_preferences=details.billing_preferences,
            custom_fields=details.custom_fields or [],
            addresses=details.addresses or [],
            lead=details.lead,
            additional_data=details.additional_data or {},
            sales_intelligence=details.sales_intelligence or {},
            social_pages=details.social_pages or [],
            work_history=details.work_history or [],
            educational_history=details.educational_history or [],
            skills=details.skills or [],
            enrichment_done=bool(details.enrichment_done),
            last_enriched_at=details.last_enriched_at,
            created_at=details.created_at,
            updated_at=details.updated_at,
        ).model_dump(exclude_none=True, mode="json"),
    )



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
async def external_update_company(
    request: Request,
    background_tasks: BackgroundTasks,
    client_id: str = Path(..., description="Client ID"),
    db_connection: asyncpg.Connection = Depends(db_conn),
    organization_id: str = Depends(external_organization_id),
    body: ExternalUpdateCompanyRequest = Body(...),
):
    """External update company endpoint (Isometrik credential auth)."""
    service = ClientService(db_connection=db_connection)
    details = await service.get_client_details(client_id, organization_id)
    if details.client_type != ClientType.COMPANY:
        return success_response(
            request=request,
            message_key="clients.errors.not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
            status_code=http_status.HTTP_404_NOT_FOUND,
        )

    update_result: dict | None = None
    internal_body = body.to_update_client_request()
    async with db_connection.transaction():
        update_result = await service.update_client(client_id, organization_id, internal_body)

    # Best-effort Typesense indexing after update, offloaded to background task.
    created_company_id = update_result.get("created_company_id") if update_result else None
    client_refs = [(client_id, organization_id)]
    if created_company_id:
        client_refs.append((created_company_id, organization_id))
    background_tasks.add_task(
        ClientService.index_clients_in_typesense_background,
        client_refs,
    )

    # Trigger enrichment only when enrichment-relevant inputs have changed.
    enrichment_input_fields = (
        "company_name",
        "industry",
        "websites",
        "social_pages",
        "addresses",
        "primary_contact",
        "profile_photo_url",
    )
    enrichment_inputs_changed = any(
        getattr(internal_body, field_name) is not None for field_name in enrichment_input_fields
    )
    if enrichment_inputs_changed:
        background_tasks.add_task(
            ClientService.trigger_enrichment_background,
            client_id,
            organization_id,
        )
    if created_company_id:
        background_tasks.add_task(
            ClientService.trigger_enrichment_background,
            created_company_id,
            organization_id,
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
async def external_update_contact(
    request: Request,
    background_tasks: BackgroundTasks,
    client_id: str = Path(..., description="Client ID"),
    db_connection: asyncpg.Connection = Depends(db_conn),
    organization_id: str = Depends(external_organization_id),
    body: ExternalUpdateContactRequest = Body(...),
):
    """External update contact endpoint (Isometrik credential auth)."""
    service = ClientService(db_connection=db_connection)
    details = await service.get_client_details(client_id, organization_id)
    if details.client_type != ClientType.PERSON:
        return success_response(
            request=request,
            message_key="clients.errors.not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
            status_code=http_status.HTTP_404_NOT_FOUND,
        )

    update_result: dict | None = None
    internal_body = body.to_update_client_request()
    async with db_connection.transaction():
        update_result = await service.update_client(client_id, organization_id, internal_body)

    # Best-effort Typesense indexing after update, offloaded to background task.
    created_company_id = update_result.get("created_company_id") if update_result else None
    client_refs = [(client_id, organization_id)]
    if created_company_id:
        client_refs.append((created_company_id, organization_id))
    background_tasks.add_task(
        ClientService.index_clients_in_typesense_background,
        client_refs,
    )

    # Trigger enrichment only when enrichment-relevant inputs have changed.
    enrichment_input_fields = (
        "company_name",
        "industry",
        "websites",
        "social_pages",
        "addresses",
        "primary_contact",
        "profile_photo_url",
    )
    enrichment_inputs_changed = any(
        getattr(internal_body, field_name) is not None for field_name in enrichment_input_fields
    )
    if enrichment_inputs_changed:
        background_tasks.add_task(
            ClientService.trigger_enrichment_background,
            client_id,
            organization_id,
        )
    if created_company_id:
        background_tasks.add_task(
            ClientService.trigger_enrichment_background,
            created_company_id,
            organization_id,
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
async def external_delete_company(
    request: Request,
    background_tasks: BackgroundTasks,
    client_id: str = Path(..., description="Client ID"),
    db_connection: asyncpg.Connection = Depends(db_conn),
    organization_id: str = Depends(external_organization_id),
):
    """External delete company endpoint (Isometrik credential auth)."""
    service = ClientService(db_connection=db_connection)
    details = await service.get_client_details(client_id, organization_id)
    if details.client_type != ClientType.COMPANY:
        return success_response(
            request=request,
            message_key="clients.errors.not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
            status_code=http_status.HTTP_404_NOT_FOUND,
        )

    async with db_connection.transaction():
        await service.delete_client(client_id, organization_id)

    # Best-effort Typesense deletion, offloaded to background task.
    background_tasks.add_task(
        ClientService.delete_clients_from_typesense_background,
        [client_id],
    )

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
async def external_delete_contact(
    request: Request,
    background_tasks: BackgroundTasks,
    client_id: str = Path(..., description="Client ID"),
    db_connection: asyncpg.Connection = Depends(db_conn),
    organization_id: str = Depends(external_organization_id),
):
    """External delete contact endpoint (Isometrik credential auth)."""
    service = ClientService(db_connection=db_connection)
    details = await service.get_client_details(client_id, organization_id)
    if details.client_type != ClientType.PERSON:
        return success_response(
            request=request,
            message_key="clients.errors.not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
            status_code=http_status.HTTP_404_NOT_FOUND,
        )

    async with db_connection.transaction():
        await service.delete_client(client_id, organization_id)

    # Best-effort Typesense deletion, offloaded to background task.
    background_tasks.add_task(
        ClientService.delete_clients_from_typesense_background,
        [client_id],
    )

    return success_response(
        request=request,
        message_key="clients.success.client_deleted",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )
