"""Webhook endpoints for external services (e.g. enrichment callbacks)."""

from typing import Any

import asyncpg
from fastapi import APIRouter, Body, Depends, Request
from fastapi import status as http_status

from apps.user_service.app.dependencies.db import db_uow
from apps.user_service.app.services.client_enrichment_service import (
    ClientEnrichmentService,
)
from libs.shared_utils.response_factory import success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


@router.post(
    "/enrichment",
    status_code=http_status.HTTP_200_OK,
    summary="Enrichment webhook",
    description="Receives callbacks from the enrichment service.",
    responses={
        http_status.HTTP_200_OK: {"description": "Webhook received"},
        http_status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Invalid payload"},
    },
)
async def enrichment_webhook(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    body: dict[str, Any] = Body(...),
):
    """Handle POST from enrichment service; process company enrichment
    when request_id and enriched_company are present."""
    payload = body.model_dump(exclude_none=True)
    if body.request_id and body.enriched_company:
        enrichment_service = ClientEnrichmentService.from_settings()
        await enrichment_service.process_company_enrichment_webhook(db_connection, payload)
    return success_response(
        request=request,
        message_key="webhooks.success.received",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )
