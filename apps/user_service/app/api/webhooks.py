"""Webhook endpoints for external services (e.g. enrichment callbacks)."""

from typing import Any

import asyncpg
from fastapi import APIRouter, BackgroundTasks, Body, Depends, Request
from fastapi import status as http_status

from apps.user_service.app.dependencies.db import db_uow
from apps.user_service.app.services.client_enrichment_service import (
    ClientEnrichmentService,
)
from apps.user_service.app.services.client_service import (
    index_clients_in_typesense_background,
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
    when request_id and enriched_company (company) or enriched_profile (person) are present.

    After applying enrichment updates, schedule a best-effort Typesense reindex so the
    search index reflects the latest enriched client data.
    """
    request_id = body.get("request_id")
    if not request_id:
        # Missing request_id means the webhook payload is invalid for our enrichment flow.
        return success_response(
            request=request,
            message_key="webhooks.errors.invalid_payload",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    enrichment_service = ClientEnrichmentService.from_settings()
    has_company_payload = body.get("enriched_company") is not None
    has_person_payload = body.get("enriched_profile") is not None

    if not (has_company_payload or has_person_payload):
        # We only process company/person enrichment webhooks; anything else is invalid.
        return success_response(
            request=request,
            message_key="webhooks.errors.invalid_payload",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    # Apply enrichment updates to the client; get (client_id, organization_id) from result.
    if has_company_payload:
        client_ref = await enrichment_service.process_company_enrichment_webhook(
            db_connection, body
        )
    else:
        client_ref = await enrichment_service.process_person_enrichment_webhook(db_connection, body)

    # Single generic background task: pass both payloads when present; service uses the right one.
    background_tasks.add_task(
        enrichment_service.fetch_and_store_sales_intelligence_for_request,
        request_id=request_id,
        enriched_company=body.get("enriched_company"),
        enriched_profile=body.get("enriched_profile"),
    )

    # Schedule Typesense reindex using client ref from enrichment processing (no extra DB call).
    if client_ref:
        client_id, organization_id = client_ref
        background_tasks.add_task(
            index_clients_in_typesense_background,
            [(client_id, organization_id)],
        )

    return success_response(
        request=request,
        message_key="webhooks.success.received",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )
