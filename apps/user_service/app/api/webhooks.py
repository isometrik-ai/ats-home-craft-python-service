"""Webhook endpoints for external services (e.g. enrichment callbacks)."""

import uuid
from typing import Any

import asyncpg
from fastapi import APIRouter, BackgroundTasks, Body, Depends, Request
from fastapi import status as http_status

from apps.user_service.app.dependencies.db import db_conn
from apps.user_service.app.dependencies.external_auth import get_organization_context
from apps.user_service.app.schemas.enums import KafkaTopics
from apps.user_service.app.services.client_enrichment_service import (
    ClientEnrichmentService,
)
from apps.user_service.app.services.event_service import EventService
from apps.user_service.app.services.typesense_index_service import (
    index_companies_background,
    index_contacts_background,
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
    db_connection: asyncpg.Connection = Depends(db_conn),
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

    # Apply enrichment updates to company or contact; get (entity_id, organization_id) from result.
    async with db_connection.transaction():
        if has_company_payload:
            client_ref = await enrichment_service.process_company_enrichment_webhook(
                db_connection, body
            )
        else:
            client_ref = await enrichment_service.process_person_enrichment_webhook(
                db_connection, body
            )

    # Store sales intelligence only for company enrichment (best-effort).
    if has_company_payload:
        background_tasks.add_task(
            enrichment_service.fetch_and_store_sales_intelligence_for_request,
            request_id=request_id,
            enriched_company=body.get("enriched_company"),
            enriched_profile=None,
        )

    # Schedule Typesense reindex using entity ref from enrichment processing (no extra DB call).
    if client_ref:
        entity_id, organization_id = client_ref
        if has_company_payload:
            background_tasks.add_task(
                index_companies_background,
                [(entity_id, organization_id)],
            )
        else:
            background_tasks.add_task(
                index_contacts_background,
                [(entity_id, organization_id)],
            )

    return success_response(
        request=request,
        message_key="webhooks.success.received",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("email notifications webhook")
@router.post(
    "/email-notifications",
    status_code=http_status.HTTP_200_OK,
    summary="Email notifications webhook",
    description="Receives email notification events and publishes a Kafka event.",
    responses={
        http_status.HTTP_200_OK: {"description": "Webhook received"},
        http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
        http_status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Invalid payload"},
    },
)
async def email_notifications_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db_connection: asyncpg.Connection = Depends(db_conn),
    organization_id: str = Depends(get_organization_context),
    body: dict[str, Any] = Body(...),
):
    """Handle POST from email notifications webhook;
    process email notification events and publish a Kafka event.

    This webhook is used to receive email notification events from external providers
    and publish a Kafka event.
    """
    # Provider-agnostic: we store and publish the raw payload without assuming a schema.
    aggregate_id = str(body.get("event_id") or uuid.uuid4())

    # Store the raw webhook payload and emit a Kafka event with:
    # - module: "email"
    # - aggregate_id: best-effort (event_id or UUID fallback)
    event_service = EventService(db_connection=db_connection)
    async with db_connection.transaction():
        event = await event_service.create_lifecycle_event(
            event_type="email.notification.received",
            aggregate_id=aggregate_id,
            organization_id=organization_id,
            actor_user_id=None,
            payload={
                "module": "email",
                "action": "received",
                "raw_event": body,
            },
            topics=[KafkaTopics.CRM_EVENTS],
        )

    background_tasks.add_task(
        EventService.publish_event_background,
        event=event,
        key=aggregate_id,
        topics=[KafkaTopics.CRM_EVENTS],
    )

    return success_response(
        request=request,
        message_key="webhooks.success.received",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
    )
