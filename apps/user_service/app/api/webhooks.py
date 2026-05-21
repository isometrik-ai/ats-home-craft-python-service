"""Webhook endpoints for external services (e.g. enrichment callbacks)."""

import uuid
from typing import Any

import asyncpg
from fastapi import APIRouter, BackgroundTasks, Body, Depends, Request
from fastapi import status as http_status

from apps.user_service.app.dependencies.db import db_conn
from apps.user_service.app.dependencies.external_auth import get_organization_context
from apps.user_service.app.schemas.enums import (
    CompanyEventType,
    ContactEventType,
    KafkaTopics,
)
from apps.user_service.app.services.client_enrichment_service import (
    ClientEnrichmentService,
)
from apps.user_service.app.services.email_notification_service import (
    EmailNotificationService,
)
from apps.user_service.app.services.event_service import EventService
from apps.user_service.app.services.typesense_index_service import (
    index_companies_background,
    index_contacts_background,
)
from apps.user_service.app.services.webhook_service import WebhookService
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

    # Schedule Typesense reindex and CRM event for Supermemory consumer.
    if client_ref:
        entity_id, organization_id = client_ref
        if has_company_payload:
            background_tasks.add_task(
                index_companies_background,
                [(entity_id, organization_id)],
            )
            enrichment_event = EventService().build_event(
                event_type=CompanyEventType.UPDATED.value,
                aggregate_id=entity_id,
                organization_id=organization_id,
                actor_user_id=None,
                payload={"module": "companies", "action": "enrichment_applied"},
            )
        else:
            background_tasks.add_task(
                index_contacts_background,
                [(entity_id, organization_id)],
            )
            enrichment_event = EventService().build_event(
                event_type=ContactEventType.UPDATED.value,
                aggregate_id=entity_id,
                organization_id=organization_id,
                actor_user_id=None,
                payload={"module": "contacts", "action": "enrichment_applied"},
            )
        background_tasks.add_task(
            EventService.publish_event_background,
            event=enrichment_event,
            key=entity_id,
            topics=[KafkaTopics.CRM_EVENTS],
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
    description=(
        "Receives inbound email events, stores content on the matching contact, "
        "and publishes Kafka lifecycle events."
    ),
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
    """Handle POST from email notifications webhook.

    Resolves the sender to a CRM contact, stores the message in Supermemory, and persists
    a lifecycle event.
    """
    aggregate_id = str(body.get("event_id") or uuid.uuid4())
    email_service = EmailNotificationService(db_connection=db_connection)

    event_service = EventService(db_connection=db_connection)
    contact_id: str | None = None
    email_stored = False
    async with db_connection.transaction():
        process_result = await email_service.process_message_received(
            organization_id=organization_id,
            webhook_body=body,
        )
        contact_id = process_result.contact_id
        email_stored = process_result.stored

        event_payload: dict[str, Any] = {
            "module": "email",
            "action": "received",
            "raw_event": body,
            "contact_id": contact_id,
            "stored_in_supermemory": email_stored,
        }
        if process_result.skipped_reason:
            event_payload["skipped_reason"] = process_result.skipped_reason
        if process_result.supermemory_document_ids:
            event_payload["supermemory_document_ids"] = list(
                process_result.supermemory_document_ids
            )

        event = await event_service.create_lifecycle_event(
            event_type="email.notification.received",
            aggregate_id=aggregate_id,
            organization_id=organization_id,
            actor_user_id=None,
            payload=event_payload,
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


@handle_api_exceptions("whatsapp notifications webhook")
@router.post(
    "/whatsapp-notifications",
    status_code=http_status.HTTP_200_OK,
    summary="WhatsApp notifications webhook",
    description=(
        "Accepts a JSON object (e.g. Timelines.ai message:new webhook). The payload is "
        "serialized to a string and sent as the Isometrik workflow ``query``."
    ),
    responses={
        http_status.HTTP_200_OK: {"description": "Workflow executed"},
        http_status.HTTP_502_BAD_GATEWAY: {"description": "Upstream request failed"},
    },
)
async def whatsapp_notifications_webhook(
    request: Request,
    body: dict[str, Any] = Body(...),
):
    """Forward webhook JSON to Isometrik: payload dict is stringified and sent as workflow query."""
    webhook_service = WebhookService()
    workflow_result = await webhook_service.execute_isometrik_whatsapp_workflow(
        webhook_payload=body,
    )
    return success_response(
        request=request,
        message_key="webhooks.success.received",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=workflow_result,
    )
