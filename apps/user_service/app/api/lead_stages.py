"""Lead Stages API Module.

This module currently provides create operation for lead stages.
"""

import asyncpg
from fastapi import APIRouter, Body, Depends, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_uow
from apps.user_service.app.schemas.lead_stages import CreateLeadStageRequest
from apps.user_service.app.services.lead_stage_service import LeadStageService
from apps.user_service.app.utils.common_utils import (
    check_permissions,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import LEADS_MANAGEMENT_CREATE
from libs.shared_utils.response_factory import success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/lead-stages", tags=["Lead Stages"])


@handle_api_exceptions("create lead stage")
@router.post(
    "",
    status_code=http_status.HTTP_201_CREATED,
    description="Create a new lead stage for the authenticated organization",
    summary="Create lead stage",
    responses={
        http_status.HTTP_201_CREATED: {"description": "Lead stage created successfully"},
        http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
        http_status.HTTP_403_FORBIDDEN: {"description": "Forbidden"},
        http_status.HTTP_409_CONFLICT: {
            "description": "Duplicate stage name or sort order conflict",
        },
        http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
        http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
    },
)
@limiter.limit("100/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="confidential",
    compliance_tags=[
        "soc2_audit",
        "audit_required",
    ],
    table_name="lead_stages",
    category="LEAD_STAGE",
)
async def create_lead_stage(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: CreateLeadStageRequest = Body(...),
):
    """Create a lead stage definition.

    Implements first-stage bootstrap, append/insert sort-order semantics,
    and uniqueness guarantees for stage_name/stage_key within organization.
    """
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=LEADS_MANAGEMENT_CREATE,
    )

    request.state.audit_table = "lead_stages"
    request.state.audit_description = f"Created lead stage: {body.stage_name}"
    request.state.audit_risk_level = "medium"
    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }

    lead_stage_service = LeadStageService(
        user_context=user_context,
        db_connection=db_connection,
    )
    created_stage = await lead_stage_service.create_lead_stage(body)
    request.state.raw_audit_new_data = created_stage

    return success_response(
        request=request,
        message_key="lead_stages.success.stage_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
    )
