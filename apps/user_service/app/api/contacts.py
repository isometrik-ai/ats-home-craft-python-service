"""Contacts API."""

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Query, Request
from fastapi import status as http_status
from supabase import AsyncClient

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.dependencies.supabase import supabase_service
from apps.user_service.app.schemas.contacts import (
    ContactDetailsResponse,
    ContactSummaryResponse,
    CreateContactRequest,
    ListContactsRequest,
    UpdateContactRequest,
)
from apps.user_service.app.services.activity_service import ActivityService
from apps.user_service.app.services.contacts_service import ContactsService
from apps.user_service.app.utils.common_utils import (
    check_permissions,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import (
    CONTACTS_MANAGEMENT_CREATE,
    CONTACTS_MANAGEMENT_DELETE,
    CONTACTS_MANAGEMENT_EDIT,
    CONTACTS_MANAGEMENT_VIEW,
)
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/contacts", tags=["Contacts"])

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
        "Creates a contact in public.contacts. Requires contact_type and email. "
        "Provisions a Supabase auth user and Isometrik identity for every contact."
    ),
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "soc2_audit", "audit_required"],
    table_name="contacts",
    category="CONTACT",
)
async def create_contact(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    sb_client: AsyncClient = Depends(supabase_service),
    body: CreateContactRequest = Body(...),
):
    """Create a contact."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=CONTACTS_MANAGEMENT_CREATE,
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
    result = await service.create_contact(body)
    contact_id = result["contact_id"]
    request.state.audit_requested_id = str(contact_id)
    request.state.audit_description = f"Created contact: {contact_id}"
    request.state.raw_audit_old_data = result.get("old_data")
    request.state.raw_audit_new_data = result.get("new_data")

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
    description="Returns paginated contacts from PostgreSQL.",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def list_contacts(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    body: ListContactsRequest = Body(...),
):
    """List contacts from PostgreSQL with pagination."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=CONTACTS_MANAGEMENT_VIEW,
    )
    service = ContactsService(db_connection=db_connection, user_context=user_context)

    dropdown_filters = [f.model_dump(mode="json") for f in body.dropdown_filters]
    result = await service.list_contacts(
        search=body.search,
        status=body.status.value if body.status else None,
        contact_type=body.contact_type.value if body.contact_type else None,
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
        permission_codes=CONTACTS_MANAGEMENT_VIEW,
    )

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
    contact_id: str = Path(..., description="Contact identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Get a single contact including addresses and linked companies."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=CONTACTS_MANAGEMENT_VIEW,
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
    ),
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "soc2_audit", "audit_required"],
    table_name="contacts",
    category="CONTACT",
)
async def update_contact(
    request: Request,
    contact_id: str = Path(..., description="Contact identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: UpdateContactRequest = Body(...),
):
    """Update a contact."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=CONTACTS_MANAGEMENT_EDIT,
    )
    service = ContactsService(db_connection=db_connection, user_context=user_context)
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
    request.state.raw_audit_old_data = result.get("old_data")
    request.state.raw_audit_new_data = result.get("new_data")

    return success_response(
        request=request,
        message_key="contacts.success.contact_updated",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("delete contact")
@router.delete(
    "/{contact_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Delete a contact (soft delete)",
    description="Soft-deletes a contact.",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "soc2_audit", "audit_required"],
    table_name="contacts",
    category="CONTACT",
)
async def delete_contact(
    request: Request,
    contact_id: str = Path(..., description="Contact identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Soft-delete a contact."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=CONTACTS_MANAGEMENT_DELETE,
    )
    service = ContactsService(db_connection=db_connection, user_context=user_context)
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

    return success_response(
        request=request,
        message_key="contacts.success.contact_deleted",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )
