"""Webhook endpoints for external services (e.g. enrichment callbacks)."""

from typing import Any

import asyncpg
from fastapi import APIRouter, BackgroundTasks, Body, Depends, Request
from fastapi import status as http_status

from apps.user_service.app.dependencies.db import db_uow
from apps.user_service.app.services.client_enrichment_service import (
    ClientEnrichmentService,
)
from apps.user_service.app.utils.common_utils import handle_api_exceptions
from libs.shared_utils.response_factory import success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


@handle_api_exceptions("enrichment webhook")
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
    background_tasks: BackgroundTasks,
    db_connection: asyncpg.Connection = Depends(db_uow),
    body: dict[str, Any] = Body(...),
):
    """Handle POST from enrichment service; process company or person enrichment
    when request_id and enriched_company (company) or enriched_profile (person) are present."""
    request_id = body.get("request_id")
    if request_id:
        enrichment_service = ClientEnrichmentService.from_settings()
        if body.get("enriched_company") is not None:
            await enrichment_service.process_company_enrichment_webhook(db_connection, body)
        elif body.get("enriched_profile") is not None:
            await enrichment_service.process_person_enrichment_webhook(db_connection, body)
            # Trigger sales intelligence fetch/store in the background so the webhook
            # response is not blocked by the external sales-intelligence service.
            background_tasks.add_task(
                enrichment_service.fetch_and_store_sales_intelligence_for_request,
                request_id=request_id,
                enriched_profile=body.get("enriched_profile"),
            )
    return success_response(
        request=request,
        message_key="webhooks.success.received",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )
