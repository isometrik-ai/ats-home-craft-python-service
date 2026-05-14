"""Superadmin organization API (system_super_admin JWT only)."""

from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Path, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.db import db_conn
from apps.user_service.app.schemas.superadmin_organizations import (
    SuperadminOrganizationListQueryParams,
)
from apps.user_service.app.services.superadmin_organization_service import (
    SuperadminOrganizationService,
)
from apps.user_service.app.utils.common_utils import (
    handle_api_exceptions,
    require_super_admin,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
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
