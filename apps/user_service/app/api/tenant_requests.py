"""Admin tenant requests API."""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Request
from fastapi import status as http_status
from supabase import AsyncClient

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.dependencies.supabase import supabase_service
from apps.user_service.app.schemas.tenant_requests import (
    ApproveTenantRequestRequest,
    RejectTenantDocumentRequest,
    TenantRequestListQuery,
)
from apps.user_service.app.services.tenant_requests_service import TenantRequestsService
from apps.user_service.app.utils.audit_context import set_audit_context
from apps.user_service.app.utils.common_utils import (
    check_permissions,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import (
    CONTACTS_MANAGEMENT_EDIT,
    CONTACTS_MANAGEMENT_VIEW,
)
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/tenant-requests", tags=["Tenant Requests"])

COMMON_ERROR_RESPONSES: dict[int | str, dict] = {
    401: {"description": "Unauthorized (missing/invalid JWT)."},
    403: {"description": "Forbidden (insufficient permissions)."},
    404: {"description": "Not found."},
    422: {"description": "Validation error."},
    429: {"description": "Too many requests (rate limited)."},
    500: {"description": "Internal server error."},
}


@handle_api_exceptions("get tenant request summary")
@router.get(
    "/summary",
    status_code=http_status.HTTP_200_OK,
    summary="Tenant request dashboard summary",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def get_tenant_request_summary(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return summary card counts for the admin tenant requests dashboard."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=CONTACTS_MANAGEMENT_VIEW,
        request=request,
    )
    service = TenantRequestsService(
        db_connection=db_connection,
        user_context=user_context,
    )
    data = await service.get_admin_summary()
    return success_response(
        request=request,
        message_key="tenant_requests.success.summary_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )


@handle_api_exceptions("list tenant requests")
@router.get(
    "",
    status_code=http_status.HTTP_200_OK,
    summary="List tenant requests",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def list_tenant_requests(
    request: Request,
    query: TenantRequestListQuery = Depends(),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return paginated tenant requests for admin review."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=CONTACTS_MANAGEMENT_VIEW,
        request=request,
    )
    service = TenantRequestsService(
        db_connection=db_connection,
        user_context=user_context,
    )
    items, total = await service.list_admin_requests(query=query)
    return list_response(
        request=request,
        items=[item.model_dump() for item in items],
        total=total,
        page=query.page,
        page_size=query.page_size,
        message_key="tenant_requests.success.list_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("get tenant request")
@router.get(
    "/{tenant_request_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Get tenant request detail",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def get_tenant_request(
    request: Request,
    tenant_request_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return one tenant request with documents and timeline."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=CONTACTS_MANAGEMENT_VIEW,
        request=request,
    )
    service = TenantRequestsService(
        db_connection=db_connection,
        user_context=user_context,
    )
    data = await service.get_admin_request(tenant_request_id=tenant_request_id)
    return success_response(
        request=request,
        message_key="tenant_requests.success.retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )


@handle_api_exceptions("verify tenant document")
@router.post(
    "/{tenant_request_id}/documents/{document_id}/verify",
    status_code=http_status.HTTP_200_OK,
    summary="Verify a tenant request document",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("60/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["audit_required"],
    table_name="tenant_request_documents",
    category="TENANT_REQUESTS",
)
async def verify_tenant_document(
    request: Request,
    tenant_request_id: str = Path(...),
    document_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Mark one document as verified."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=CONTACTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = TenantRequestsService(
        db_connection=db_connection,
        user_context=user_context,
    )
    data = await service.verify_document(
        tenant_request_id=tenant_request_id,
        document_id=document_id,
    )
    set_audit_context(
        request,
        user_context,
        table="tenant_request_documents",
        requested_id=document_id,
        description=f"Verified tenant document: {document_id}",
        risk_level="medium",
        new_data=data.model_dump(),
    )
    return success_response(
        request=request,
        message_key="tenant_requests.success.document_verified",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )


@handle_api_exceptions("reject tenant document")
@router.post(
    "/{tenant_request_id}/documents/{document_id}/reject",
    status_code=http_status.HTTP_200_OK,
    summary="Reject a tenant request document",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("60/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["audit_required"],
    table_name="tenant_request_documents",
    category="TENANT_REQUESTS",
)
async def reject_tenant_document(
    request: Request,
    tenant_request_id: str = Path(...),
    document_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: RejectTenantDocumentRequest = Body(...),
):
    """Reject one document with a reason."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=CONTACTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = TenantRequestsService(
        db_connection=db_connection,
        user_context=user_context,
    )
    data = await service.reject_document(
        tenant_request_id=tenant_request_id,
        document_id=document_id,
        body=body,
    )
    set_audit_context(
        request,
        user_context,
        table="tenant_request_documents",
        requested_id=document_id,
        description=f"Rejected tenant document: {document_id}",
        risk_level="medium",
        new_data=data.model_dump(),
    )
    return success_response(
        request=request,
        message_key="tenant_requests.success.document_rejected",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )


@handle_api_exceptions("approve tenant request")
@router.post(
    "/{tenant_request_id}/approve",
    status_code=http_status.HTTP_200_OK,
    summary="Approve a tenant request",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "audit_required"],
    table_name="tenant_requests",
    category="TENANT_REQUESTS",
)
async def approve_tenant_request(
    request: Request,
    tenant_request_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    sb_client: AsyncClient = Depends(supabase_service),
    body: ApproveTenantRequestRequest = Body(...),
):
    """Approve a ready tenant request and create the tenant contact + unit link."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=CONTACTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = TenantRequestsService(
        db_connection=db_connection,
        user_context=user_context,
        supabase_client=sb_client,
    )
    data = await service.approve_request(
        tenant_request_id=tenant_request_id,
        body=body,
    )
    set_audit_context(
        request,
        user_context,
        table="tenant_requests",
        requested_id=tenant_request_id,
        description=f"Approved tenant request: {tenant_request_id}",
        risk_level="high",
        new_data=data.model_dump(),
    )
    return success_response(
        request=request,
        message_key="tenant_requests.success.approved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )
