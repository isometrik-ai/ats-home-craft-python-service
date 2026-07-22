"""Owner-facing tenant request API (contact onboarding context)."""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Request
from fastapi import status as http_status
from supabase import AsyncClient

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_uow
from apps.user_service.app.dependencies.supabase import supabase_service
from apps.user_service.app.schemas.tenant_requests import (
    CreateTenantRequestRequest,
    OwnerTenantRequestListQuery,
    ReuploadTenantDocumentRequest,
)
from apps.user_service.app.services.tenant_requests_service import TenantRequestsService
from apps.user_service.app.utils.audit_context import set_audit_context
from apps.user_service.app.utils.common_utils import (
    extract_onboarding_contact_context,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(
    prefix="/contact-onboarding/tenant-requests",
    tags=["Contact Onboarding"],
)

COMMON_ERROR_RESPONSES: dict[int | str, dict] = {
    401: {"description": "Unauthorized (missing/invalid JWT)."},
    403: {"description": "Forbidden."},
    404: {"description": "Not found."},
    409: {"description": "Conflict."},
    422: {"description": "Validation error."},
    429: {"description": "Too many requests (rate limited)."},
    500: {"description": "Internal server error."},
}


@handle_api_exceptions("list owner tenant requests")
@router.get(
    "",
    status_code=http_status.HTTP_200_OK,
    summary="List tenant requests submitted by the owner",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("60/minute")
async def list_owner_tenant_requests(
    request: Request,
    query: OwnerTenantRequestListQuery = Depends(),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return paginated tenant request history for the authenticated owner."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = TenantRequestsService(
        db_connection=db_connection,
        user_context=user_context,
        supabase_client=None,
    )
    items, total = await service.list_owner_requests(
        owner_contact_id=str(contact["id"]),
        query=query,
    )
    return list_response(
        request=request,
        items=[item.model_dump() for item in items],
        total=total,
        page=query.page,
        page_size=query.page_size,
        message_key="tenant_requests.success.list_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("create tenant request")
@router.post(
    "",
    status_code=http_status.HTTP_201_CREATED,
    summary="Submit a tenant request for admin review",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "audit_required"],
    table_name="tenant_requests",
    category="CONTACT_ONBOARDING",
)
async def create_tenant_request(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    sb_client: AsyncClient = Depends(supabase_service),
    body: CreateTenantRequestRequest = Body(...),
):
    """Create and submit a tenant request with all required documents."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = TenantRequestsService(
        db_connection=db_connection,
        user_context=user_context,
        supabase_client=sb_client,
    )
    data = await service.create_request(
        owner_contact_id=str(contact["id"]),
        body=body,
    )
    set_audit_context(
        request,
        user_context,
        table="tenant_requests",
        requested_id=data.id,
        description=f"Submitted tenant request: {data.id}",
        risk_level="high",
        new_data=data.model_dump(),
    )
    return success_response(
        request=request,
        message_key="tenant_requests.success.created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=data.model_dump(),
    )


@handle_api_exceptions("get owner tenant request")
@router.get(
    "/{tenant_request_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Get tenant request detail with timeline",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("60/minute")
async def get_owner_tenant_request(
    request: Request,
    tenant_request_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return one tenant request including documents, events, and milestones."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = TenantRequestsService(
        db_connection=db_connection,
        user_context=user_context,
    )
    data = await service.get_owner_request(
        owner_contact_id=str(contact["id"]),
        tenant_request_id=tenant_request_id,
    )
    return success_response(
        request=request,
        message_key="tenant_requests.success.retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )


@handle_api_exceptions("cancel tenant request")
@router.post(
    "/{tenant_request_id}/cancel",
    status_code=http_status.HTTP_200_OK,
    summary="Cancel an in-flight tenant request",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["audit_required"],
    table_name="tenant_requests",
    category="CONTACT_ONBOARDING",
)
async def cancel_tenant_request(
    request: Request,
    tenant_request_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Cancel a pending tenant request."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = TenantRequestsService(
        db_connection=db_connection,
        user_context=user_context,
    )
    data = await service.cancel_request(
        owner_contact_id=str(contact["id"]),
        tenant_request_id=tenant_request_id,
    )
    set_audit_context(
        request,
        user_context,
        table="tenant_requests",
        requested_id=tenant_request_id,
        description=f"Cancelled tenant request: {tenant_request_id}",
        risk_level="medium",
        new_data=data.model_dump(),
    )
    return success_response(
        request=request,
        message_key="tenant_requests.success.cancelled",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )


@handle_api_exceptions("reupload tenant document")
@router.patch(
    "/{tenant_request_id}/documents/{document_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Re-upload a rejected tenant document",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["audit_required"],
    table_name="tenant_request_documents",
    category="CONTACT_ONBOARDING",
)
async def reupload_tenant_document(
    request: Request,
    tenant_request_id: str = Path(...),
    document_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: ReuploadTenantDocumentRequest = Body(...),
):
    """Replace a rejected document file and resubmit for review."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = TenantRequestsService(
        db_connection=db_connection,
        user_context=user_context,
    )
    data = await service.reupload_document(
        owner_contact_id=str(contact["id"]),
        tenant_request_id=tenant_request_id,
        document_id=document_id,
        body=body,
    )
    set_audit_context(
        request,
        user_context,
        table="tenant_request_documents",
        requested_id=document_id,
        description=f"Re-uploaded tenant document: {document_id}",
        risk_level="medium",
        new_data=data.model_dump(),
    )
    return success_response(
        request=request,
        message_key="tenant_requests.success.document_reuploaded",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )
