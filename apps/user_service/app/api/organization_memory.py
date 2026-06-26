"""Organization memory (Graphiti) natural-language CRM query API."""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Body, Depends, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.db import db_conn
from apps.user_service.app.schemas.org_memory import (
    OrgMemoryQueryBody,
    OrgMemoryQueryResponse,
)
from apps.user_service.app.services.org_memory_query_service import (
    OrgMemoryQueryService,
)
from apps.user_service.app.services.organization_memory_service import (
    is_organization_memory_enabled,
    require_org_memory_query_access,
)
from apps.user_service.app.utils.common_utils import (
    extract_user_context,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.http_exceptions import (
    ForbiddenException,
    ServiceUnavailableException,
)
from libs.shared_utils.response_factory import success_response
from libs.shared_utils.status_codes import CustomStatusCode
from libs.shared_utils.graphiti_service import is_graphiti_configured

router = APIRouter(prefix="/organization/memory", tags=["Organization Memory"])


@handle_api_exceptions("organization memory query")
@router.post(
    "/query",
    status_code=http_status.HTTP_200_OK,
    summary="Ask a natural-language question about CRM data in org memory",
    response_model=None,
)
@limiter.limit("30/minute")
async def post_org_memory_query(
    request: Request,
    body: OrgMemoryQueryBody = Body(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Graphiti hybrid search + snapshot context → grounded answer (OpenAI)."""
    user_context = await extract_user_context(current_user, db_connection)
    await require_org_memory_query_access(
        db_connection=db_connection,
        user_context=user_context,
    )

    if not is_graphiti_configured():
        raise ServiceUnavailableException(
            message_key="errors.service_unavailable",
            custom_code=CustomStatusCode.SERVICE_UNAVAILABLE,
            params={"reason": "graphiti_not_configured"},
        )

    org_id = user_context.organization_id
    assert org_id is not None
    if not await is_organization_memory_enabled(db_connection, org_id):
        raise ForbiddenException(
            message_key="organizations.errors.organization_memory_disabled",
            custom_code=CustomStatusCode.FORBIDDEN,
        )

    answer = await OrgMemoryQueryService().run(
        user_message=body.query,
        organization_id=org_id,
        entity_id=body.entity_id.strip() if body.entity_id else None,
        entity_type=body.entity_type,
        db_connection=db_connection,
    )
    payload = OrgMemoryQueryResponse(answer=answer)
    return success_response(
        request=request,
        message_key="success.retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=payload.model_dump(mode="json"),
    )
