"""Superadmin organization API.

Most routes require ``system_super_admin`` JWT. ``POST /impersonate/exit`` is an exception:
it must be called with the **impersonated user's** Bearer token to revoke that session.
"""

from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Path, Request
from fastapi import status as http_status
from supabase import AsyncClient

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn
from apps.user_service.app.dependencies.supabase import supabase_service
from apps.user_service.app.schemas.superadmin_organizations import (
    SuperadminOrganizationListQueryParams,
)
from apps.user_service.app.services.superadmin_organization_service import (
    SuperadminOrganizationService,
)
from apps.user_service.app.utils.common_utils import (
    extract_user_context,
    handle_api_exceptions,
    require_super_admin,
)
from libs.shared_middleware.jwt_auth import extract_user_data, get_user_from_auth
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/superadmin/organizations", tags=["Superadmin Organizations"])


@handle_api_exceptions("superadmin list organizations")
@router.get(
    "",
    status_code=http_status.HTTP_200_OK,
    summary="List organizations (superadmin)",
    description="Paginated organization list for platform super admins. Excludes deleted orgs.",
)
@limiter.limit("100/minute")
async def superadmin_list_organizations(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    params: SuperadminOrganizationListQueryParams = Depends(),
    current_user: dict = Depends(get_user_from_auth),
):
    """List organizations for superadmin"""
    await require_super_admin(current_user)
    service = SuperadminOrganizationService(db_connection=db_connection)
    result = await service.list_organizations(
        page=params.page,
        page_size=params.page_size,
        search=params.search,
        plan=params.plan,
        status=params.status,
        sort=params.sort,
        order=params.order,
    )
    if not result.items:
        return list_response(
            request=request,
            items=[],
            total=0,
            message_key="success.no_data",
            page=params.page,
            page_size=params.page_size,
            status_code=http_status.HTTP_200_OK,
            custom_code=CustomStatusCode.NO_CONTENT,
        )
    return list_response(
        request=request,
        items=result.items,
        total=result.total_count,
        message_key="success.retrieved",
        page=params.page,
        page_size=params.page_size,
        status_code=http_status.HTTP_200_OK,
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("superadmin exit impersonation")
@router.post(
    "/impersonate/exit",
    status_code=http_status.HTTP_200_OK,
    summary="Exit impersonation (revoke impersonated session)",
    description=(
        "Revokes the **current** Supabase auth session. Call with the **impersonated user's** "
        "Bearer access token (the session returned from impersonate), not the superadmin token. "
        "Invalidates the session in the database (same pattern as session revoke); no Redis."
    ),
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="confidential",
    compliance_tags=["gdpr", "pii", "soc2_audit", "audit_required"],
    table_name="organizations",
    category="SUPERADMIN_IMPERSONATION_END",
)
async def superadmin_exit_impersonation(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """End impersonation by revoking the impersonated user's auth session."""
    user_context = await extract_user_context(current_user, db_connection)
    request.state.audit_table = "organizations"
    request.state.audit_requested_id = str(current_user.get("session_id") or "")
    request.state.audit_description = (
        "Impersonation ended: auth session revoked for user "
        f"{user_context.user_id} (session_id={current_user.get('session_id')})"
    )
    request.state.audit_risk_level = "high"
    request.state.raw_audit_new_data = {
        "session_id": str(current_user.get("session_id") or ""),
        "user_id": user_context.user_id,
    }
    org_id = user_context.organization_id or "no_organization"
    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": org_id,
    }

    service = SuperadminOrganizationService(db_connection=db_connection)
    data = await service.exit_impersonation_session(current_user=current_user)

    return success_response(
        request=request,
        message_key="organizations.success.impersonation_session_ended",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=data,
    )


@handle_api_exceptions("superadmin impersonate organization owner")
@router.post(
    "/{organization_id}/impersonate",
    status_code=http_status.HTTP_200_OK,
    summary="Impersonate organization owner (superadmin)",
    description=(
        "Issues a Supabase session for the organization's primary owner member via "
        "admin magic-link exchange. Rate-limited; audited as high risk."
    ),
)
@limiter.limit("10/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="confidential",
    compliance_tags=["gdpr", "pii", "soc2_audit", "audit_required"],
    table_name="organizations",
    category="SUPERADMIN_IMPERSONATION",
)
async def superadmin_impersonate_organization_owner(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    supabase_admin: AsyncClient = Depends(supabase_service),
    current_user: dict = Depends(get_user_from_auth),
    organization_id: UUID = Path(..., description="Organization UUID"),
):
    """Create an owner session for support / superadmin workflows."""
    await require_super_admin(current_user)
    service = SuperadminOrganizationService(db_connection=db_connection)
    data = await service.impersonate_organization_owner(
        organization_id=str(organization_id),
        supabase_admin_client=supabase_admin,
    )

    actor_id, _, _ = extract_user_data(current_user)
    user_id = actor_id or str(current_user.get("sub") or "")
    request.state.audit_table = "organizations"
    request.state.audit_requested_id = str(organization_id)
    request.state.audit_description = (
        "Superadmin impersonation: session issued for organization owner "
        f"(organization_id={organization_id}, impersonated_user_id={data.impersonated_user_id})"
    )
    request.state.audit_risk_level = "critical"
    request.state.raw_audit_new_data = {
        "organization_id": str(organization_id),
        "impersonated_user_id": data.impersonated_user_id,
    }
    request.state.audit_user_context = {
        "user_id": user_id,
        "organization_id": str(organization_id),
    }

    return success_response(
        request=request,
        message_key="organizations.success.impersonation_session_created",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=data.model_dump(exclude_none=False),
    )


@handle_api_exceptions("superadmin get organization")
@router.get(
    "/{organization_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Get organization by id (superadmin)",
)
@limiter.limit("100/minute")
async def superadmin_get_organization(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    organization_id: UUID = Path(..., description="Organization UUID"),
):
    """Get organization detail for superadmin"""
    await require_super_admin(current_user)
    service = SuperadminOrganizationService(db_connection=db_connection)
    data = await service.get_organization_detail(str(organization_id))
    return success_response(
        request=request,
        message_key="success.retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=data.model_dump(exclude_none=False),
    )
