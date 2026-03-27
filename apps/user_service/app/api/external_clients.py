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

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.external_auth import external_organization_id
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.schemas.clients import UpdateClientRequest
from apps.user_service.app.schemas.enums import ClientStatus, ClientType
from apps.user_service.app.services.client_service import ClientService
from apps.user_service.app.utils.common_utils import handle_api_exceptions
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

# External integrations should not share the same path-space as internal `/clients/*`
# to avoid collisions with `/clients/{client_id}` routes.
router = APIRouter(prefix="/integrations/clients", tags=["Clients (External)"])


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
    return list_response(
        request=request,
        items=clients or [],
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
    return list_response(
        request=request,
        items=clients or [],
        total=total or 0,
        page=page,
        page_size=page_size,
        message_key="clients.success.clients_retrieved",
        custom_code=CustomStatusCode.SUCCESS if clients else CustomStatusCode.NO_CONTENT,
        status_code=http_status.HTTP_200_OK,
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
        data=details.model_dump(exclude_none=True),
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
        data=details.model_dump(exclude_none=True),
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
    body: UpdateClientRequest = Body(...),
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
    async with db_connection.transaction():
        update_result = await service.update_client(client_id, organization_id, body)

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
        getattr(body, field_name) is not None for field_name in enrichment_input_fields
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
    body: UpdateClientRequest = Body(...),
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
    async with db_connection.transaction():
        update_result = await service.update_client(client_id, organization_id, body)

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
        getattr(body, field_name) is not None for field_name in enrichment_input_fields
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
